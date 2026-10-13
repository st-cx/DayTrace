import datetime as dt
import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class SystemHistoryTests(unittest.TestCase):
    def history(self):
        self.assertIsNotNone(importlib.util.find_spec('daytrace_core.system_history'),
                             '需要独立的系统历史线索模块，不能伪造自动工时')
        from daytrace_core import system_history
        return system_history

    def test_complete_power_interval_is_not_app_usage(self):
        h = self.history()
        result = h.summarize_history({'events': [{'id': 12, 'time': 100}, {'id': 13, 'time': 400}],
                                      'apps': [], 'recent': [], 'errors': []}, now=500)
        self.assertEqual(result['closed_power_seconds'], 300)
        self.assertEqual(result['sessions'][0]['duration'], 300)
        self.assertIn('睡眠', result['notice'])
        self.assertNotIn('records', result)

    def test_missing_shutdown_and_repeated_boot_are_not_filled(self):
        h = self.history()
        result = h.summarize_history({'events': [{'id': 13, 'time': 80}, {'id': 12, 'time': 100},
                                                {'id': 12, 'time': 300}], 'apps': [], 'recent': [], 'errors': []}, now=500)
        self.assertEqual(result['closed_power_seconds'], 0)
        self.assertEqual(len(result['sessions']), 2)
        self.assertTrue(all(s['duration'] is None for s in result['sessions']))
        self.assertEqual(result['sessions'][1]['status'], 'shutdown_not_observed')

    def test_application_clue_is_exact_last_time_without_duration_or_path(self):
        h = self.history()
        result = h.summarize_history({'events': [], 'apps': [
            {'app': r'\Device\HarddiskVolume3\private\code.exe', 'time': 400},
            {'app': 'code.exe', 'time': 400}, {'app': 'invalid.exe', 'time': 900}],
            'recent': [], 'errors': []}, now=500)
        self.assertEqual(len(result['apps']), 1)
        self.assertEqual(result['apps'][0]['app'], 'code.exe')
        self.assertNotIn('duration', result['apps'][0])
        self.assertNotIn('path', result['apps'][0])

    def test_unavailable_sources_remain_visible_and_empty(self):
        h = self.history()
        result = h.summarize_history({'events': [], 'apps': [], 'recent': [], 'errors': ['bam unavailable']}, now=500)
        self.assertEqual(result['errors'], ['bam unavailable'])
        self.assertEqual(result['closed_power_seconds'], 0)

    def test_windows_filetime_utc_conversion_and_bounds(self):
        h = self.history()
        epoch = 116444736000000000
        self.assertEqual(h.filetime_timestamp((epoch + 1700000000 * 10000000).to_bytes(8, 'little')), 1700000000)
        self.assertIsNone(h.filetime_timestamp(b'bad'))
        self.assertIsNone(h.filetime_timestamp((0).to_bytes(8, 'little')))


if __name__ == '__main__':
    unittest.main()
