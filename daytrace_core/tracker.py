import ctypes
from ctypes import wintypes
from pathlib import Path
import re
import sys
import threading
import time

IS_WIN = sys.platform == 'win32'
BROWSERS = ('chrome.exe', 'msedge.exe', 'brave.exe', 'opera.exe', 'firefox.exe')


def foreground():
    if not IS_WIN:
        return None
    user, kernel = ctypes.windll.user32, ctypes.windll.kernel32
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    window = user.GetForegroundWindow()
    if not window:
        return None
    buffer = ctypes.create_unicode_buffer(min(user.GetWindowTextLengthW(window) + 1, 1024))
    user.GetWindowTextW(window, buffer, len(buffer))
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(window, ctypes.byref(pid))
    handle = kernel.OpenProcess(0x1000, False, pid.value)
    app = 'unknown.exe'
    if handle:
        path = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(path))
        try:
            if kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                app = Path(path.value).name.lower()
        finally:
            kernel.CloseHandle(handle)
    return {'app': app, 'title': buffer.value, 'pid': pid.value}


def idle_for():
    if not IS_WIN:
        return 0
    class LastInput(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.UINT), ('dwTime', wintypes.DWORD)]
    info = LastInput()
    info.cbSize = ctypes.sizeof(info)
    ctypes.windll.user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LastInput)]
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        raise OSError('无法读取系统空闲时间')
    ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    return ((ctypes.windll.kernel32.GetTickCount64() - info.dwTime) & 0xffffffff) / 1000


class Tracker(threading.Thread):
    daemon = True

    def __init__(self, store, clock=None, foreground_provider=None, idle_provider=None):
        super().__init__(name='DayTrace-tracker')
        self.store = store
        self.clock = clock or time.time
        self.foreground_provider = foreground_provider or foreground
        self.idle_provider = idle_provider or idle_for
        self.stop_flag = threading.Event()
        self.sample_lock = threading.RLock()
        self.browser_lock = threading.RLock()
        self.browsers = {}
        self.browser = {'domain': '', 'time': 0}
        self.previous = None
        self.last = None
        self.last_error = ''
        self.last_sample = 0
        self.last_state = 'starting'
        self.store.on_settings_change = self.change_settings

    def set_browser(self, domain, browser='chrome.exe', focused=True, private=False, client_id=''):
        if not isinstance(domain, str) or len(domain) > 250 or (domain and (not re.fullmatch(r'[a-z0-9.-]+', domain) or '..' in domain)):
            return False
        if browser not in BROWSERS or not isinstance(focused, bool) or not isinstance(private, bool):
            return False
        with self.browser_lock:
            state = {'domain': '' if private or not focused else domain.lower(), 'time': self.clock(),
                     'focused': focused, 'private': private, 'client_id': str(client_id)[:80]}
            self.browsers[browser] = state
            self.browser = state
        return True

    def state(self):
        settings = self.store.settings
        if settings['paused']:
            self.last_state = 'paused'
            return None
        if self.idle_provider() >= settings['idle_seconds']:
            self.last_state = 'idle'
            return {'app': '', 'title': '', 'domain': '', 'category': '离开电脑'}
        window = self.foreground_provider()
        if not window:
            self.last_state = 'unavailable'
            return None
        app, title = window['app'].lower(), window.get('title', '')
        if any(value in app for value in settings['excluded_apps']) or any(value in title.lower() for value in settings['excluded_titles']):
            self.last_state = 'excluded'
            return None
        domain = ''
        if settings['browser_enabled'] and app in BROWSERS:
            with self.browser_lock:
                browser = self.browsers.get(app)
                if browser and browser.get('private') and self.clock() - browser['time'] < 36:
                    self.last_state = 'excluded'
                    return None
                if browser and browser['focused'] and 0 <= self.clock() - browser['time'] < 36:
                    domain = browser['domain']
            if any(value in domain for value in settings['excluded_domains']):
                self.last_state = 'excluded'
                return None
        title = title[:200] if settings['record_titles'] else ''
        self.last_state = 'recording'
        return {'app': app, 'title': title, 'domain': domain, **self.store.classify_details(app, title, domain)}

    def _flush(self, now):
        if self.last is not None and self.previous is not None and 0 < now - self.last <= 20:
            self.store.add_interval(self.last, now, self.previous)
        elif self.last is not None and (now < self.last or now - self.last > 20):
            self.store.reset_interval()

    def change_settings(self, settings):
        with self.sample_lock:
            now = self.clock()
            self._flush(now)
            result = self.store._commit_settings(settings)
            self.previous = None
            self.last = now
            self.store.reset_interval()
            with self.browser_lock:
                if not settings['browser_enabled']:
                    self.browsers.clear()
            self.last_state = 'paused' if settings['paused'] else 'starting'
            return result

    def sample(self, now=None):
        now = self.clock() if now is None else now
        with self.sample_lock:
            # Settings transitions flush and clear the old sample synchronously.
            self._flush(now)
            self.store.checkpoint_timer(now)
            try:
                self.previous = self.state()
                self.last_error = ''
            except Exception as exc:
                self.previous = None
                self.last_state = 'error'
                self.last_error = type(exc).__name__
            if self.previous is None:
                self.store.reset_interval()
            self.last = self.last_sample = now

    def health(self):
        return {'collector_alive': self.is_alive(), 'last_sample': self.last_sample,
                'state': self.last_state, 'error': self.last_error, 'browser_last_seen':
                {key: value['time'] for key, value in self.browsers.items()}}

    def run(self):
        while not self.stop_flag.is_set():
            try:
                self.sample()
            except Exception as exc:
                self.last_error = type(exc).__name__
                self.last_state = 'error'
            self.stop_flag.wait(5)
        with self.sample_lock:
            self._flush(self.clock())
            self.previous = None
            self.store.stop_timer(reason='app_stopped')

    def stop(self):
        self.stop_flag.set()
