import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tkinter as tk
from daytrace_panel import CompactPanel

class CompactPanelTests(unittest.TestCase):
    def test_real_day_payload_renders_totals_and_small_window(self):
        root = tk.Tk(); root.withdraw()
        try:
            panel = CompactPanel(lambda *args: {}, 'http://127.0.0.1:41897', root)
            panel.render({'settings':{},'health':{'state':'recording'},'colors':{'学习':'#b7ead4'}},
                         {'total':3661,'totals':{'学习':1200},'records':[]}, {})
            root.update_idletasks()
            self.assertEqual(panel.total.cget('text'), '1h 01m')
            self.assertIn('20m', panel.invest.cget('text'))
            self.assertEqual(root.winfo_width(), 360)
            self.assertEqual(root.winfo_height(), 460)
            self.assertTrue(panel.chart.find_all())
        finally:
            root.destroy()

if __name__ == '__main__': unittest.main()
