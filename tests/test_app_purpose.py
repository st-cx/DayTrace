import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from daytrace_core.store import Store
from daytrace_core.tracker import Tracker


class AppPurposeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.sqlite3'
        self.store = Store(self.path)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_basic_categories_use_exact_normalized_application_names(self):
        expected = {'Code.ExE': '编程', r'C:\Program Files\JetBrains\PYCHARM64.EXE': '编程',
                    'Obsidian.exe': '学习', 'SumatraPDF.exe': '学习', 'WINWORD.EXE': '工作',
                    'vlc.exe': '娱乐', 'mumunxdevice.exe': '游戏', 'ticktick.exe': '生活',
                    'msedge.exe': '浏览器', 'chatgpt.exe': '其他', 'weixin.exe': '其他',
                    'javaw.exe': '其他', 'notcode.exe': '其他', 'myvlc.exe': '其他', 'unknown.exe': '其他'}
        for app, category in expected.items():
            with self.subTest(app=app):
                self.assertEqual(self.store.classify(app, '', ''), category)

    def auto_record(self, app='chatgpt.exe'):
        self.store.add_interval(100, 110, {'app': app, 'title': '', 'domain': '', 'category': self.store.classify(app, '', '')})
        return self.store.query(0, 200)[-1]['id']

    def test_correction_updates_record_next_sample_and_survives_restart(self):
        before = copy.deepcopy(self.store.settings)
        rid = self.auto_record()
        self.store.update_record(rid, {'category': '学习', 'remember_app': True})
        self.assertEqual(self.store.query(0, 200)[0]['category'], '学习')
        tracker = Tracker(self.store, foreground_provider=lambda: {'app': 'CHATGPT.EXE', 'title': 'private title'}, idle_provider=lambda: 0)
        state = tracker.state()
        self.assertEqual(state['category'], '学习')
        self.assertEqual(state['title'], '')
        self.assertEqual(state['domain'], '')
        self.assertEqual(self.store.settings, before)
        self.store.db.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.classify('chatgpt.exe', '', ''), '学习')
        self.assertEqual(self.store.classify('notchatgpt.exe', '', ''), '其他')

    def test_existing_user_rule_has_priority_over_remembered_and_basic_purpose(self):
        rid = self.auto_record('code.exe')
        self.store.update_record(rid, {'category': '学习', 'remember_app': True})
        self.store.add_rule('code.exe', '工作', target='app', project='Homework', tags=['Priority'])
        self.assertEqual(self.store.classify_details('code.exe', '', ''), {'category': '工作', 'project': 'Homework', 'tags': ['Priority']})

    def test_app_memory_is_in_backup_and_restored_to_new_store(self):
        rid = self.auto_record('weixin.exe')
        self.store.update_record(rid, {'category': '生活', 'remember_app': True})
        backup = self.store.export_backup()
        other = Store(Path(self.tmp.name) / 'restored.sqlite3')
        try:
            other.import_backup(backup)
            self.assertEqual(other.classify('WEIXIN.EXE', '', ''), '生活')
            self.assertEqual(other.import_backup(backup)['imported'], 0)
        finally:
            other.db.close()

    def test_invalid_correction_never_changes_record_or_memory(self):
        rid = self.auto_record()
        before = self.store.query(0, 200)
        for changes in ({'category': 'Wrong', 'remember_app': True}, {'category': '学习', 'remember_app': 'true'},
                        {'category': '学习', 'remember_app': True, 'end': 99},
                        {'category': '学习', 'remember_app': True, 'app': 'other.exe'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.update_record(rid, changes)
            self.assertEqual(self.store.query(0, 200), before)
            self.assertEqual(self.store.classify('chatgpt.exe', '', ''), '其他')

    def test_mapping_write_failure_rolls_back_historical_edit(self):
        rid = self.auto_record()
        self.store.db.execute("CREATE TRIGGER deny_app_mapping BEFORE INSERT ON app_categories BEGIN SELECT RAISE(ABORT,'test map failure'); END")
        self.store.db.commit()
        before = self.store.query(0, 200)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.update_record(rid, {'category': '学习', 'project': 'Rollback', 'remember_app': True})
        self.assertEqual(self.store.query(0, 200), before)
        self.assertEqual(self.store.projects(), [])
        self.assertEqual(self.store.classify('chatgpt.exe', '', ''), '其他')

    def test_unchecked_and_manual_edit_do_not_change_future_app_category(self):
        rid = self.auto_record('msedge.exe')
        self.store.update_record(rid, {'category': '学习'})
        self.assertEqual(self.store.classify('msedge.exe', '', ''), '浏览器')
        with self.assertRaises(ValueError):
            self.store.update_record(rid, {'category': '学习', 'remember_app': True})




import test_upgrade as api_support


class AppPurposeApiTests(unittest.TestCase):
    setUp = api_support.UpgradeApiTests.setUp
    tearDown = api_support.UpgradeApiTests.tearDown
    request = api_support.UpgradeApiTests.request

    def test_http_correction_and_visible_purpose_preserve_privacy(self):
        import datetime as dt
        app = api_support.app
        start = dt.datetime.now().replace(hour=8, minute=0, second=0, microsecond=0).timestamp()
        app.STORE.add_interval(start, start + 10, {'app': 'chatgpt.exe', 'category': '其他', 'title': '', 'domain': ''})
        rid = app.STORE.query(start, start + 11)[0]['id']
        result = json.loads(self.request('/api/record/' + str(rid), 'PUT', {'category': '学习', 'remember_app': True}))
        self.assertEqual(result['record']['category'], '学习')
        response = json.loads(self.request('/api/day?date=' + dt.date.today().isoformat()))
        row = response['records'][0]
        self.assertEqual(row['app_purpose']['display_name'], 'ChatGPT')
        self.assertEqual(row['app_purpose']['remembered_category'], '学习')
        self.assertEqual(app.STORE.classify('chatgpt.exe', '', ''), '学习')
        self.assertFalse(app.STORE.settings['record_titles'])
        self.assertFalse(app.STORE.settings['browser_enabled'])

    def test_invalid_memory_backup_is_rejected_before_any_writes(self):
        from urllib.error import HTTPError
        app = api_support.app
        before = app.STORE.export_backup()
        payload = copy.deepcopy(before)
        payload['app_categories'] = [{'app': 'C:\\wrong\\chatgpt.exe', 'category': '学习'}]
        with self.assertRaises(HTTPError) as error:
            self.request('/api/import', 'POST', payload)
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(app.STORE.export_backup()['records'], before['records'])
        self.assertEqual(app.STORE.export_backup()['app_categories'], [])


if __name__ == '__main__':
    unittest.main()
