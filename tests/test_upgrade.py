import csv
import io
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import quote
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daytrace as app


class UpgradeStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'daytrace.sqlite3'
        self.store = app.Store(self.path)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_extension_pairing_survives_restart(self):
        first = getattr(self.store, 'browser_token', self.store.token)
        other = app.Store(self.path)
        try:
            self.assertEqual(first, getattr(other, 'browser_token', other.token))
        finally:
            other.db.close()

    def test_invalid_settings_are_rejected_without_partial_update(self):
        with self.assertRaises(ValueError):
            self.store.update_settings({'record_titles': True, 'excluded_apps': 'keepass.exe'})
        self.assertFalse(self.store.settings['record_titles'])

    def test_corrupt_setting_types_fall_back_to_defaults(self):
        self.store.settings_path.write_text(json.dumps({'idle_seconds': 'oops', 'paused': 'false', 'excluded_apps': 'bad'}), encoding='utf-8')
        other = app.Store(self.path)
        try:
            self.assertIsInstance(other.settings['idle_seconds'], int)
            self.assertFalse(other.settings['paused'])
            self.assertIsInstance(other.settings['excluded_apps'], list)
        finally:
            other.db.close()

    def test_legacy_records_migrate_without_loss(self):
        rid = self.store.add_manual(100, 200, '学习', '旧记录')
        self.store.db.close()
        self.store = app.Store(self.path)
        record = self.store.query(0, 300)[0]
        self.assertEqual(record['id'], rid)
        self.assertEqual(record['note'], '旧记录')
        self.assertEqual(record.get('tags'), [])
        self.assertEqual(record.get('project'), '')

    def test_timer_does_not_double_count_auto_and_survives_restart(self):
        self.assertTrue(callable(getattr(self.store, 'start_timer', None)), '缺少开始/停止任务计时')
        with patch('time.time', return_value=100):
            self.store.start_timer({'category': '学习', 'note': '复习', 'project': '课程', 'tags': ['数学']})
        self.store.checkpoint_timer(110)
        self.store.add_interval(100, 110, {'app': 'code.exe', 'title': '', 'domain': '', 'category': '编程'})
        merged = app.reconcile(self.store.query(0, 300), 0, 300)
        self.assertEqual(sum(r['end'] - r['start'] for r in merged), 10)
        self.assertEqual(merged[0]['category'], '学习')
        self.store.db.close()
        self.store = app.Store(self.path)
        self.assertFalse(self.store.timer_status()['running'])
        self.assertEqual(self.store.query(0, 300)[-1]['end'], 110)

    def test_backup_import_is_atomic_and_deduplicated(self):
        self.assertTrue(callable(getattr(self.store, 'export_backup', None)), '缺少完整备份恢复')
        self.store.add_manual(100, 200, '学习', '真实记录')
        data = self.store.export_backup()
        result = self.store.import_backup(data)
        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['skipped'], 1)
        data['records'].append({**data['records'][0], 'start': 500, 'end': 400})
        with self.assertRaises(ValueError):
            self.store.import_backup(data)
        self.assertEqual(len(self.store.query(0, 1000)), 1)

    def test_rule_reapply_updates_only_auto_records(self):
        self.assertTrue(callable(getattr(self.store, 'apply_rules', None)), '缺少规则回溯重分类')
        self.store.add_interval(100, 110, {'app': 'code.exe', 'title': '', 'domain': '', 'category': '其他'})
        self.store.add_manual(120, 150, '生活', '手动')
        self.store.add_rule('code.exe', '编程')
        result = self.store.apply_rules(0, 200, dry_run=True)
        self.assertEqual(result['changed'], 1)
        self.assertEqual(self.store.query(0, 200)[0]['category'], '其他')
        self.store.apply_rules(0, 200)
        self.assertEqual([r['category'] for r in self.store.query(0, 200)], ['编程', '生活'])

    def test_backup_restores_project_metadata_in_a_new_database(self):
        self.store.save_project({'name': '课程', 'color': '#123456', 'hourly_rate': 30, 'budget_minutes': 180})
        self.store.add_manual(100, 200, '学习', '活动', project='课程')
        other = app.Store(Path(self.tmp.name) / 'other.sqlite3')
        try:
            other.import_backup(self.store.export_backup())
            project = other.projects()[0]
            self.assertEqual(project['color'], '#123456')
            self.assertEqual(project['hourly_rate'], 30)
            self.assertEqual(project['budget_minutes'], 180)
        finally:
            other.db.close()

    def test_backup_reimport_preserves_notes_exactly(self):
        self.store.db.execute('INSERT INTO records(start,end,category,source,note) VALUES(100,200,"学习","manual","  旧笔记  ")')
        self.store.db.commit()
        result = self.store.import_backup(self.store.export_backup())
        self.assertEqual(result['imported'], 0)
        self.assertEqual(self.store.query(0, 300)[0]['note'], '  旧笔记  ')

    def test_focus_deadline_and_sleep_do_not_count_unobserved_time(self):
        with patch('time.time', return_value=100):
            self.store.start_timer({'category': '学习', 'focus_minutes': 1})
        for now in range(105, 165, 5):
            self.store.checkpoint_timer(now)
        self.assertEqual(self.store.timer_status()['last_reason'], 'focus_completed')
        self.assertEqual(self.store.query(0, 300)[0]['end'], 160)
        with patch('time.time', return_value=200):
            self.store.start_timer({'category': '学习'})
        self.store.checkpoint_timer(210)
        self.store.checkpoint_timer(1000)
        self.assertEqual(self.store.timer_status()['last_reason'], 'sleep')
        self.assertEqual(self.store.query(0, 1500)[-1]['end'], 210)

    def test_actual_v01_schema_is_migrated_without_replacing_ids(self):
        legacy_path = Path(self.tmp.name) / 'legacy' / 'db.sqlite3'
        legacy_path.parent.mkdir()
        db = sqlite3.connect(legacy_path)
        db.execute('CREATE TABLE records (id INTEGER PRIMARY KEY, start REAL NOT NULL, end REAL NOT NULL, app TEXT NOT NULL DEFAULT "", title TEXT NOT NULL DEFAULT "", domain TEXT NOT NULL DEFAULT "", category TEXT NOT NULL, source TEXT NOT NULL, note TEXT NOT NULL DEFAULT "")')
        db.execute('CREATE TABLE rules (id INTEGER PRIMARY KEY, pattern TEXT NOT NULL, category TEXT NOT NULL)')
        db.execute('INSERT INTO records(id,start,end,category,source,note) VALUES(42,100,200,"学习","manual","旧数据")')
        db.execute('INSERT INTO rules VALUES(5,"code.exe","编程")')
        db.commit()
        db.close()
        other = app.Store(legacy_path)
        try:
            self.assertEqual(other.query(0, 300)[0]['id'], 42)
            self.assertEqual(other.rules()[0]['id'], 5)
            self.assertEqual(other.query(0, 300)[0]['tags'], [])
            self.assertEqual(other.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        finally:
            other.db.close()


class UpgradeTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = app.Store(Path(self.tmp.name) / 'test.sqlite3')

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_pause_flushes_only_up_to_change_and_drops_old_title(self):
        self.assertTrue(callable(getattr(app.Tracker, 'sample', None)), '缺少可验证的采样边界')
        now = [100.0]
        tracker = app.Tracker(self.store, clock=lambda: now[0], foreground_provider=lambda: {'app': 'code.exe', 'title': '文档'}, idle_provider=lambda: 0)
        self.store.update_settings({'record_titles': True})
        tracker.sample()
        now[0] = 102
        self.store.update_settings({'paused': True, 'record_titles': False})
        now[0] = 105
        tracker.sample()
        records = self.store.query(0, 200)
        self.assertEqual(sum(r['end'] - r['start'] for r in records), 2)
        self.assertEqual(max(r['end'] for r in records), 102)

    def test_browser_source_and_clear_event_avoid_stale_domain(self):
        self.assertTrue(callable(getattr(app.Tracker, 'sample', None)))
        window = [{'app': 'chrome.exe', 'title': '普通页面'}]
        tracker = app.Tracker(self.store, foreground_provider=lambda: window[0], idle_provider=lambda: 0)
        self.store.update_settings({'browser_enabled': True})
        tracker.set_browser('example.com', 'chrome.exe')
        self.assertEqual(tracker.state()['domain'], 'example.com')
        window[0] = {'app': 'msedge.exe', 'title': '普通页面'}
        self.assertEqual(tracker.state()['domain'], '')
        window[0] = {'app': 'chrome.exe', 'title': '新标签页'}
        tracker.set_browser('', 'chrome.exe')
        self.assertEqual(tracker.state()['domain'], '')

    def test_sleep_gap_is_not_charged_to_previous_application(self):
        self.assertTrue(callable(getattr(app.Tracker, 'sample', None)))
        now = [100.0]
        tracker = app.Tracker(self.store, clock=lambda: now[0], foreground_provider=lambda: {'app': 'code.exe', 'title': ''}, idle_provider=lambda: 0)
        tracker.sample()
        now[0] = 105
        tracker.sample()
        now[0] = 400
        tracker.sample()
        self.assertEqual(sum(r['end'] - r['start'] for r in self.store.query(0, 1000)), 5)


class UpgradeApiTests(unittest.TestCase):
    def test_system_history_requires_token_and_never_adds_work_records(self):
        from urllib.error import HTTPError
        before = app.STORE.export_backup()['records']
        with patch('daytrace_core.system_history.scan_history', return_value={'evidence_only': True, 'sessions': []}):
            result = json.loads(self.request('/api/system-history'))
        self.assertTrue(result['evidence_only'])
        self.assertEqual(before, app.STORE.export_backup()['records'])
        with self.assertRaises(HTTPError) as rejected:
            urlopen(f'http://127.0.0.1:{app.PORT}/api/system-history')
        self.assertEqual(rejected.exception.code, 403)

    def test_external_csv_import_api_preserves_seconds_options_and_atomicity(self):
        from test_clockify_import import fixture, focus_fixture
        from urllib.error import HTTPError
        first = json.loads(self.request('/api/import', 'POST', {'csv': fixture(), 'csv_options': {'date_order': 'dmy', 'utc_offset_minutes': 480}}))
        second = json.loads(self.request('/api/import', 'POST', {'csv': focus_fixture(), 'csv_options': {'date_order': 'mdy', 'utc_offset_minutes': 480}}))
        self.assertEqual((first['imported'], second['imported']), (1, 1))
        self.assertTrue(Path(first['backup_path']).exists())
        records = app.STORE.export_backup()['records']
        self.assertEqual(sum(row['end'] - row['start'] for row in records), 89)
        self.assertEqual(json.loads(self.request('/api/import', 'POST', {'csv': fixture()}))['skipped'], 1)
        with self.assertRaises(HTTPError) as failed:
            self.request('/api/import', 'POST', {'csv': fixture({}, {'Duration (h)': '00:00:55'})})
        self.assertEqual(failed.exception.code, 400)
        self.assertEqual(len(app.STORE.export_backup()['records']), 2)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        app.STORE = app.Store(Path(self.tmp.name) / 'test.sqlite3')
        app.TRACKER = app.Tracker(app.STORE)
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.previous_port = app.PORT
        app.PORT = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        app.STORE.db.close()
        app.PORT = self.previous_port
        self.tmp.cleanup()

    def request(self, path, method='GET', data=None):
        headers = {'X-DayTrace-Key': app.STORE.token, 'Content-Type': 'application/json'}
        req = Request(f'http://127.0.0.1:{app.PORT}' + path, data=json.dumps(data).encode() if data is not None else None, method=method, headers=headers)
        with urlopen(req, timeout=3) as response:
            return response.read()

    def test_csv_effective_export_matches_dashboard_and_clips_dates(self):
        a, b = app.day_bounds('2026-10-12')
        app.STORE.add_interval(a, a + 20, {'app': 'code.exe', 'title': '', 'domain': '', 'category': '编程'})
        app.STORE.add_manual(a + 5, a + 15, '学习', '')
        raw = self.request('/api/export?from=2026-10-12&to=2026-10-12').decode('utf-8-sig')
        rows = list(csv.reader(io.StringIO(raw)))
        total = sum(float(row[2]) * 60 for row in rows[1:])
        self.assertAlmostEqual(total, 20, delta=0.7)

    def test_reverse_export_range_is_rejected(self):
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError) as cm:
            self.request('/api/export?from=2026-10-12&to=2026-10-11')
        self.assertEqual(cm.exception.code, 400)

    def test_records_can_be_edited_and_searched(self):
        self.assertTrue(hasattr(app.STORE, 'update_record'))
        a, b = app.day_bounds('2026-10-12')
        rid = app.STORE.add_manual(a + 100, a + 200, '生活', '旧描述')
        self.request(f'/api/record/{rid}', 'PUT', {'category': '学习', 'note': '复习线性代数', 'project': '课程', 'tags': ['数学']})
        out = json.loads(self.request('/api/records?from=2026-10-12&to=2026-10-12&q=' + quote('线性代数')))
        self.assertEqual(out['total_count'], 1)
        self.assertEqual(out['records'][0]['project'], '课程')

    def test_persistent_extension_key_does_not_authorize_settings(self):
        from urllib.error import HTTPError
        req = Request(f'http://127.0.0.1:{app.PORT}/api/settings', data=b'{"paused":true}',
                      headers={'X-DayTrace-Key': app.STORE.browser_token, 'Content-Type': 'application/json'})
        with self.assertRaises(HTTPError) as cm:
            urlopen(req, timeout=3)
        self.assertEqual(cm.exception.code, 403)

    def test_cross_origin_requests_are_rejected_even_with_session_key(self):
        from urllib.error import HTTPError
        req = Request(f'http://127.0.0.1:{app.PORT}/api/settings', data=b'{"paused":true}',
                      headers={'X-DayTrace-Key': app.STORE.token, 'Content-Type': 'application/json', 'Origin': 'https://untrusted.example'})
        with self.assertRaises(HTTPError) as cm:
            urlopen(req, timeout=3)
        self.assertEqual(cm.exception.code, 403)

    def test_cross_midnight_export_is_clipped_and_matches_day_report(self):
        a, b = app.day_bounds('2026-10-12')
        app.STORE.add_manual(a - 600, a + 600, '休息', '睡觉')
        raw = self.request('/api/export?from=2026-10-12&to=2026-10-12').decode('utf-8-sig')
        rows = list(csv.reader(io.StringIO(raw)))
        self.assertEqual(float(rows[1][2]), 10)
        self.assertTrue(rows[1][0].startswith('2026-10-12 00:00:00'))
        day = json.loads(self.request('/api/day?date=2026-10-12'))
        self.assertEqual(day['total'], 600)

    def test_csv_formula_cells_are_safe_for_spreadsheets(self):
        a, b = app.day_bounds('2026-10-12')
        app.STORE.add_manual(a, a + 60, '学习', '=2+2')
        raw = self.request('/api/export?from=2026-10-12&to=2026-10-12').decode('utf-8-sig')
        rows = list(csv.reader(io.StringIO(raw)))
        self.assertEqual(rows[1][8], "'=2+2")


if __name__ == '__main__':
    unittest.main()
