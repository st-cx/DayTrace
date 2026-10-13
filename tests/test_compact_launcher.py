import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import manage_daytrace as manager

class CompactLauncherTests(unittest.TestCase):
    def test_default_start_opens_compact_panel_not_browser(self):
        self.assertTrue(callable(getattr(manager, 'show_panel', None)), '默认入口需要紧凑桌面面板')
        with patch.object(manager, 'config', return_value={'app':'daytrace'}), patch.object(manager, 'show_panel') as panel, patch.object(manager.webbrowser, 'open') as browser:
            manager.start()
        panel.assert_called_once()
        browser.assert_not_called()

    def test_background_login_start_never_opens_ui(self):
        self.assertTrue(callable(getattr(manager, 'show_panel', None)), '需要分离后台启动与面板')
        with patch.object(manager, 'config', return_value={'app':'daytrace'}), patch.object(manager, 'show_panel') as panel, patch.object(manager.webbrowser, 'open') as browser:
            manager.start(background=True)
        panel.assert_not_called()
        browser.assert_not_called()

if __name__ == '__main__': unittest.main()
