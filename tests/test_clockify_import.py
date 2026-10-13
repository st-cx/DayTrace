import csv
import datetime as dt
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from daytrace_core.store import Store

HEADERS = ['Project', 'Client', 'Description', 'Task', 'User', 'Group', 'Email', 'Tags',
           'Billable', 'Start Date', 'Start Time', 'End Date', 'End Time', 'Duration (h)',
           'Duration (decimal)', 'Billable Rate (USD)', 'Billable Amount (USD)']


def fixture(*changes):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, HEADERS)
    writer.writeheader()
    for change in changes or ({},):
        row = dict.fromkeys(HEADERS, '')
        row.update({'Description': 'DayTrace 逆向验证：计时与报表', 'User': 'Test User',
                    'Email': 'fixture@example.invalid', 'Billable': 'No', 'Start Date': '12/10/2026',
                    'Start Time': '16:03:28', 'End Date': '12/10/2026', 'End Time': '16:04:24',
                    'Duration (h)': '00:00:56', 'Duration (decimal)': '0.02'})
        row.update(change)
        writer.writerow(row)
    return out.getvalue()


def focus_fixture(**change):
    fields = ['Task', 'Description', 'Date', 'Start and stop time', 'Logged time', 'Project',
              'Member', 'Email', 'Client', 'Billable', 'Amount', 'Tags']
    row = {'Task': '-', 'Description': 'DayTrace 逆向验证：Focus 计时', 'Date': '10/12/2026',
           'Start and stop time': '16:03:35 - 16:04:08', 'Logged time': '0:00:33', 'Project': '-',
           'Member': 'Test User', 'Email': 'fixture@example.invalid', 'Client': '-',
           'Billable': 'No', 'Amount': '0.00', 'Tags': '-'}
    row.update(change)
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fields)
    writer.writeheader()
    writer.writerow(row)
    return out.getvalue()


class ClockifyImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'daytrace.sqlite3')

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def records(self):
        return self.store.export_backup()['records']

    def test_exact_duration_and_local_time_deduplicate(self):
        result = self.store.import_backup({'csv': fixture()})
        self.assertEqual(result['imported'], 1)
        row = self.records()[0]
        self.assertEqual(row['end'] - row['start'], 56)
        self.assertEqual(row['start'], dt.datetime(2026, 10, 12, 16, 3, 28).timestamp())
        self.assertEqual(row['note'], 'DayTrace 逆向验证：计时与报表')
        self.assertEqual(self.store.import_backup({'csv': fixture()})['skipped'], 1)
        self.assertFalse(self.store.settings['record_titles'])
        self.assertFalse(self.store.settings['browser_enabled'])

    def test_inexact_or_contradictory_clockify_rows_reject_entire_file(self):
        for bad in ({'Duration (h)': '00:00:55'}, {'Duration (h)': ''},
                    {'Start Time': ''}, {'End Time': '16:02:00'}, {'Billable': 'Maybe'},
                    {'Start Date': '31/02/2026'}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.store.import_backup({'csv': fixture({}, bad)})
                self.assertEqual(self.records(), [])

    def test_date_order_and_explicit_offset(self):
        self.store.import_backup({'csv': fixture(), 'csv_options': {'date_order': 'mdy', 'utc_offset_minutes': 480}})
        self.assertEqual(self.records()[0]['start'], dt.datetime(2026, 12, 10, 16, 3, 28, tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp())
        for opts in ({'date_order': 'guess'}, {'utc_offset_minutes': True}, {'utc_offset_minutes': 9999}, {'utc_offset_minutes': 0.5}):
            with self.subTest(opts=opts), self.assertRaises(ValueError):
                self.store.import_backup({'csv': fixture(), 'csv_options': opts})

    def test_team_unknown_and_broken_headers_reject_without_writes(self):
        for content in (fixture({}, {'Email': 'other@example.invalid'}),
                        fixture({}, {'User': 'Other User', 'Email': 'fixture@example.invalid'}),
                        'Unknown,Something\nx,y\n', 'Unknown,Something\n',
                        fixture().replace('Duration (h)', 'Rounded duration'),
                        fixture().replace('Project,Client', 'Project,Project'),
                        fixture() + 'extra,columns\n'):
            with self.subTest(content=content[:30]), self.assertRaises(ValueError):
                self.store.import_backup({'csv': content})
            self.assertEqual(self.records(), [])

    def test_project_tags_client_task_and_billable_preserved_without_foreign_rate(self):
        self.store.import_backup({'csv': fixture({'Project': 'Courses', 'Tags': 'Python, Java',
                                                 'Client': 'School', 'Task': 'Lab', 'Billable': 'Yes',
                                                 'Billable Rate (USD)': '50'})})
        row = self.records()[0]
        self.assertEqual(row['project'], 'Courses')
        self.assertEqual(row['tags'], ['Python', 'Java'])
        self.assertTrue(row['billable'])
        self.assertEqual(row['hourly_rate'], 0)
        self.assertEqual(row['note'], 'DayTrace 逆向验证：计时与报表\n客户：School\n任务：Lab')
        self.assertEqual(self.store.projects()[0]['hourly_rate'], 0)

    def test_toggl_focus_exact_seconds_source_date_and_deduplication(self):
        self.store.import_backup({'csv': focus_fixture()})
        row = self.records()[0]
        self.assertEqual(row['start'], dt.datetime(2026, 10, 12, 16, 3, 35).timestamp())
        self.assertEqual(row['end'] - row['start'], 33)
        self.assertEqual(row['project'], '')
        self.assertEqual(row['tags'], [])
        self.assertEqual(row['note'], 'DayTrace 逆向验证：Focus 计时')
        self.assertEqual(row['app'], 'Toggl Focus')
        self.assertEqual(self.store.import_backup({'csv': focus_fixture()})['skipped'], 1)

    def test_focus_cross_midnight_and_contradictions(self):
        self.store.import_backup({'csv': focus_fixture(**{'Start and stop time': '23:59:50 - 00:00:23'})})
        row = self.records()[0]
        self.assertEqual(row['end'] - row['start'], 33)
        self.assertEqual(dt.datetime.fromtimestamp(row['end']).day, 13)
        for content in (focus_fixture(**{'Logged time': '0:00:34'}), focus_fixture(**{'Logged time': ''}),
                        focus_fixture(**{'Start and stop time': '16:03 - 16:04'}),
                        focus_fixture(**{'Billable': 'Maybe'}), focus_fixture(**{'Date': '2/30/2026'}),
                        focus_fixture(**{'Member': '', 'Email': ''}),
                        focus_fixture().replace('Logged time', 'Rounded time')):
            with self.subTest(content=content[:30]), self.assertRaises(ValueError):
                self.store.import_backup({'csv': content})
            self.assertEqual(len(self.records()), 1)

    def test_legacy_daytrace_csv_and_json_remain_compatible(self):
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(['开始', '结束', '分类', '来源', '备注', '项目', '标签', '计费', '时薪'])
        writer.writerow(['2026-10-12 16:03:28', '2026-10-12 16:04:24', '学习', 'manual', "'=SUM(A1)", '课程', '甲|乙', '1', '25'])
        self.store.import_backup({'csv': out.getvalue()})
        row = self.records()[0]
        self.assertEqual(row['note'], '=SUM(A1)')
        self.assertEqual(row['tags'], ['甲', '乙'])
        self.assertEqual(row['hourly_rate'], 25)
        self.assertEqual(self.store.import_backup(self.store.export_backup())['skipped'], 1)

    def test_long_metadata_rejects_without_truncation_or_partial_write(self):
        for changes in ({'Description': 'x' * 501}, {'Description': 'x' * 497, 'Client': 'abc'},
                        {'Project': 'x' * 101}, {'Tags': 'x' * 41}):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                self.store.import_backup({'csv': fixture({}, changes)})
            self.assertEqual(self.records(), [])



if __name__ == '__main__':
    unittest.main()
