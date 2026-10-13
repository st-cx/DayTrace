import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from daytrace_core.store import Store
from daytrace_core.timeline import reconcile, summarize
import daytrace_api as app


class TimerFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'timer.sqlite3'
        self.store = Store(self.path)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def restart(self):
        self.store.db.close()
        self.store = Store(self.path)

    def start_task(self, focus=25):
        with patch('daytrace_core.store.time.time', return_value=100):
            self.store.start_timer({'category': '工作', 'note': '整理资料', 'project': '课程',
                                    'tags': ['备考'], 'billable': True, 'hourly_rate': 360,
                                    'focus_minutes': focus})
        self.store.checkpoint_timer(110)


class TimerRecoveryTests(TimerFixture):
    def test_crash_restart_preserves_context_without_counting_downtime(self):
        self.start_task()
        self.restart()
        status = self.store.timer_status(100000)
        self.assertFalse(status['running'])
        self.assertIsNotNone(status.get('recovery'), '重启后应提供停止的任务恢复候选')
        self.assertEqual(status['recovery']['entry']['note'], '整理资料')
        self.assertEqual(status['recovery']['entry']['tags'], ['备考'])
        self.assertEqual(status['recovery']['entry']['hourly_rate'], 360)
        self.assertEqual(status['recovery']['stopped_at'], 110)
        self.assertEqual(self.store.query(0, 200000)[0]['end'], 110)

    def test_resume_creates_new_piece_with_fresh_focus_deadline(self):
        self.start_task()
        self.restart()
        candidate = self.store.timer_status(100000)['recovery']
        self.assertTrue(callable(getattr(self.store, 'resume_timer', None)), '缺少明确恢复决策')
        with patch('daytrace_core.store.time.time', return_value=100000):
            resumed = self.store.resume_timer(candidate['recovery_id'])
        self.assertTrue(resumed['running'])
        self.assertIsNone(resumed.get('recovery'))
        self.assertNotEqual(resumed['entry']['id'], candidate['entry']['id'])
        self.assertEqual(resumed['entry']['start'], 100000)
        self.assertEqual(resumed['focus_remaining'], 1500)
        self.store.checkpoint_timer(100010)
        report = summarize(reconcile(self.store.query(0, 200000), 0, 200000))
        self.assertEqual(report['total'], 20)
        self.assertEqual(report['billable_seconds'], 20)
        self.assertEqual(report['estimated_amount'], 2)
        self.assertEqual([row['end'] for row in self.store.query(0, 200000)], [110, 100010])

    def test_dismiss_only_clears_candidate_and_preserves_paid_time(self):
        self.start_task()
        self.restart()
        candidate = self.store.timer_status()['recovery']
        self.assertTrue(callable(getattr(self.store, 'dismiss_recovery', None)), '缺少保留工时的放弃继续决策')
        dismissed = self.store.dismiss_recovery(candidate['recovery_id'])
        self.assertFalse(dismissed['running'])
        self.assertIsNone(dismissed['recovery'])
        self.restart()
        self.assertIsNone(self.store.timer_status()['recovery'])
        report = summarize(reconcile(self.store.query(0, 200000), 0, 200000))
        self.assertEqual(report['total'], 10)
        self.assertEqual(report['estimated_amount'], 1)

    def test_candidate_survives_multiple_restarts_with_same_decision_id(self):
        self.start_task()
        self.restart()
        first = self.store.timer_status()['recovery']
        for _ in range(3):
            self.restart()
            self.assertEqual(self.store.timer_status()['recovery'], first)

    def test_unrelated_start_cannot_silently_replace_pending_task(self):
        self.start_task()
        self.restart()
        candidate = self.store.timer_status()['recovery']
        with self.assertRaises(ValueError):
            self.store.start_timer({'note': '另一个任务'})
        self.assertEqual(self.store.timer_status()['recovery'], candidate)

    def test_clean_service_stop_preserves_candidate_to_the_shutdown_instant(self):
        self.start_task()
        self.store.stop_timer(112, reason='app_stopped')
        status = self.store.timer_status(100000)
        self.assertIsNotNone(status['recovery'], '正常退出服务也应保留任务上下文')
        self.assertEqual(status['recovery']['stopped_at'], 112)
        self.assertEqual(status['recovery']['reason'], 'app_stopped')
        self.restart()
        self.assertEqual(self.store.timer_status()['recovery']['stopped_at'], 112)

    def test_user_stop_keeps_legacy_finished_behavior(self):
        self.start_task()
        self.store.stop_timer(112)
        self.restart()
        self.assertFalse(self.store.timer_status()['running'])
        self.assertIsNone(self.store.timer_status()['recovery'])
        self.assertEqual(self.store.query(0, 200000)[0]['end'], 112)

    def test_explicit_stop_after_unobserved_gap_finishes_without_candidate(self):
        self.start_task()
        status = self.store.stop_timer(100000)
        self.assertFalse(status['running'])
        self.assertIsNone(status['recovery'], '用户明确结束任务时，不能因采样空档留下待恢复任务')
        self.assertEqual(self.store.query(0, 200000)[0]['end'], 110)

    def test_no_timer_database_still_has_no_candidate(self):
        self.restart()
        self.assertFalse(self.store.timer_status()['running'])
        self.assertIsNone(self.store.timer_status()['recovery'])

    def test_tampered_candidate_cannot_change_original_task_or_charge_rate(self):
        self.start_task()
        self.restart()
        candidate = copy.deepcopy(self.store.timer_status()['recovery'])
        candidate['entry']['hourly_rate'] = 999999
        self.store.db.execute('UPDATE meta SET value=? WHERE key="recovery_timer"', (json.dumps(candidate),))
        self.store.db.commit()
        self.restart()
        self.assertIsNone(self.store.timer_status()['recovery'], '损坏或陈旧的恢复候选必须丢弃，不得覆盖真实条目')
        self.assertEqual(self.store.query(0, 200000)[0]['hourly_rate'], 360)

    def test_malformed_meta_does_not_break_startup_or_modify_existing_time(self):
        self.store.add_manual(100, 110, '工作', '历史工时')
        for key, value in [('active_timer', '{broken'), ('active_timer', '[]'), ('recovery_timer', '{broken'),
                           ('recovery_timer', '{"recovery_id":"bogus","entry":{"id":999}}')]:
            with self.subTest(key=key, value=value):
                self.store.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, value))
                self.store.db.commit()
                try:
                    self.restart()
                except Exception as exc:
                    self.fail('损坏状态应安全丢弃，不能令软件启动失败: ' + type(exc).__name__)
                self.assertIsNone(self.store.timer_status()['recovery'])
                self.assertEqual(self.store.query(0, 200000)[0]['end'], 110)

    def test_editing_old_record_invalidates_candidate_without_undoing_edit(self):
        self.start_task()
        self.restart()
        candidate = self.store.timer_status()['recovery']
        self.store.update_record(candidate['entry']['id'], {'note': '已确认并修改'})
        self.assertIsNone(self.store.timer_status()['recovery'])
        with self.assertRaises(ValueError):
            self.store.resume_timer(candidate['recovery_id'])
        self.assertEqual(self.store.query(0, 200000)[0]['note'], '已确认并修改')

    def test_candidate_referencing_non_timer_record_is_not_recoverable(self):
        rid = self.store.add_manual(100, 110, '工作', '已确认活动')
        entry = self.store.query(0, 200)[0]
        active = {'record_id': rid, 'record': {key: value for key, value in entry.items() if key != 'id'},
                  'focus_minutes': 25, 'last': 110, 'deadline': 1600}
        self.store.db.execute('INSERT OR REPLACE INTO meta VALUES("active_timer",?)', (json.dumps(active),))
        self.store.db.commit()
        self.restart()
        self.assertIsNone(self.store.timer_status()['recovery'])
        self.assertEqual(self.store.query(0, 200)[0]['source'], 'manual')

    def test_unbounded_record_id_in_candidate_does_not_break_startup(self):
        candidate = {'recovery_id': 'synthetic-valid-length', 'entry': {'id': 10 ** 100, 'focus_minutes': 25},
                     'reason': 'recovered', 'stopped_at': 110}
        self.store.db.execute('INSERT OR REPLACE INTO meta VALUES("recovery_timer",?)', (json.dumps(candidate),))
        self.store.db.commit()
        try:
            self.restart()
        except OverflowError:
            self.fail('候选ID应有数据库整数范围校验')
        self.assertIsNone(self.store.timer_status()['recovery'])

    def test_resume_failure_rolls_back_new_piece_and_keeps_candidate(self):
        self.start_task()
        self.restart()
        candidate = self.store.timer_status()['recovery']
        before = self.store.export_backup()['records']
        with patch.object(self.store, '_ensure_project', side_effect=RuntimeError('simulated write failure')):
            with self.assertRaises(RuntimeError):
                self.store.resume_timer(candidate['recovery_id'])
        self.assertFalse(self.store.timer_status()['running'], '恢复失败不能留下内存中的运行状态')
        self.assertEqual(self.store.timer_status()['recovery'], candidate)
        self.assertEqual(self.store.export_backup()['records'], before)
        self.restart()
        self.assertEqual(self.store.timer_status()['recovery'], candidate)

    def test_zero_second_task_still_preserves_context_but_no_charged_time(self):
        with patch('daytrace_core.store.time.time', return_value=100):
            self.store.start_timer({'note': '刚开始的任务', 'billable': True, 'hourly_rate': 360})
        self.store.stop_timer(100, reason='shutdown')
        self.restart()
        self.assertEqual(self.store.timer_status()['recovery']['entry']['note'], '刚开始的任务')
        self.assertEqual(summarize(reconcile(self.store.query(0, 200000), 0, 200000))['estimated_amount'], 0)

    def test_sleep_and_clock_changes_stop_before_gap_and_allow_explicit_recovery(self):
        for gap_now, reason in [(100000, 'sleep'), (90, 'clock_changed')]:
            with self.subTest(reason=reason):
                self.start_task(focus=0)
                self.store.checkpoint_timer(gap_now)
                status = self.store.timer_status()
                self.assertFalse(status['running'])
                self.assertEqual(status['recovery']['reason'], reason)
                self.assertEqual(status['recovery']['stopped_at'], 110)
                self.store.dismiss_recovery(status['recovery']['recovery_id'])

    def test_duplicate_or_stale_recovery_decision_cannot_create_extra_piece(self):
        self.start_task()
        self.restart()
        recovery_id = self.store.timer_status()['recovery']['recovery_id']
        with self.assertRaises(ValueError):
            self.store.resume_timer('old-tab-id')
        self.store.resume_timer(recovery_id)
        count = self.store.db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        with self.assertRaises(ValueError):
            self.store.resume_timer(recovery_id)
        with self.assertRaises(ValueError):
            self.store.dismiss_recovery(recovery_id)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM records').fetchone()[0], count)


class TimerRecoveryApiTests(TimerFixture):
    def setUp(self):
        TimerFixture.setUp(self)
        self.start_task()
        self.restart()
        self.previous = app.STORE, app.TRACKER, app.PORT
        app.STORE = self.store
        app.TRACKER = app.Tracker(self.store)
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        app.PORT = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{app.PORT}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        app.STORE, app.TRACKER, app.PORT = self.previous
        TimerFixture.tearDown(self)

    def request(self, path, data=None, key=True):
        headers = {'Content-Type': 'application/json'}
        if key:
            headers['X-DayTrace-Key'] = self.store.token
        request = Request(self.base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
            return json.load(response)

    def test_api_resume_creates_exactly_one_current_piece_and_requires_key(self):
        candidate = self.request('/api/timer')['recovery']
        with self.assertRaises(HTTPError) as denied:
            self.request('/api/timer/resume', {'recovery_id': candidate['recovery_id']}, key=False)
        self.assertEqual(denied.exception.code, 403)
        with patch('daytrace_core.store.time.time', return_value=100000):
            try:
                status = self.request('/api/timer/resume', {'recovery_id': candidate['recovery_id']})
            except HTTPError as error:
                self.fail('恢复接口应存在并成功处理决策: ' + str(error.code))
        self.assertEqual(status['entry']['start'], 100000)
        self.assertEqual(status['focus_remaining'], 1500)
        with self.assertRaises(HTTPError) as stale:
            self.request('/api/timer/resume', {'recovery_id': candidate['recovery_id']})
        self.assertEqual(stale.exception.code, 400)

    def test_api_dismiss_keeps_record_and_removes_banner_candidate(self):
        candidate = self.request('/api/timer')['recovery']
        try:
            status = self.request('/api/timer/dismiss-recovery', {'recovery_id': candidate['recovery_id']})
        except HTTPError as error:
            self.fail('放弃继续接口应存在并成功处理决策: ' + str(error.code))
        self.assertIsNone(status['recovery'])
        self.assertEqual(self.store.query(0, 200000)[0]['end'], 110)
        self.assertEqual(self.request('/api/config')['version'], '0.4.0')


if __name__ == '__main__':
    unittest.main()
