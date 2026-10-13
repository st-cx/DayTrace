"""Small desktop overview. Closing this window leaves the collector running."""
import datetime as dt
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path

BG, SURFACE, TEXT, MUTED, ACCENT = '#303438', '#282c31', '#eef5fb', '#a3adbb', '#b7ead4'
WIDTH, HEIGHT = 360, 460


def short_duration(seconds):
    n = max(0, int(seconds or 0))
    return f'{n // 3600}h {n % 3600 // 60:02d}m' if n >= 3600 else f'{n // 60}m {n % 60:02d}s'


class CompactPanel:
    def __init__(self, request, base, root=None):
        self.request, self.base = request, base
        self.root = root or tk.Tk()
        self.root.title('DayTrace · 时迹')
        self.root.geometry(f'{WIDTH}x{HEIGHT}')
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self.root.protocol('WM_DELETE_WINDOW', self.root.destroy)
        self.root.option_add('*Font', ('Microsoft YaHei UI', 9))
        try:
            icon = Path(__file__).resolve().parent / 'assets/DayTrace.ico'
            if icon.is_file(): self.root.iconbitmap(str(icon))
        except tk.TclError:
            pass
        self.queue, self.busy, self.cfg = queue.Queue(), False, None
        self.topmost = False
        frame = tk.Frame(self.root, bg=BG, padx=14, pady=10)
        frame.pack(fill='both', expand=True)
        head = tk.Frame(frame, bg=BG)
        head.pack(fill='x')
        tk.Label(head, text='◷  DayTrace', bg=BG, fg=TEXT, font=('Segoe UI', 13, 'bold')).pack(side='left')
        self.button(head, '置顶', self.pin).pack(side='right')
        self.status = tk.Label(frame, text='连接后台…', bg=BG, fg=ACCENT, anchor='w', font=('Microsoft YaHei UI', 8))
        self.status.pack(fill='x', pady=(4, 9))
        stats = tk.Frame(frame, bg=SURFACE, padx=12, pady=9)
        stats.pack(fill='x')
        labels = tk.Frame(stats, bg=SURFACE)
        labels.pack(fill='x')
        tk.Label(labels, text='今日有效记录', bg=SURFACE, fg=MUTED, font=('Microsoft YaHei UI', 8)).pack(side='left')
        tk.Label(labels, text=dt.date.today().strftime('%m.%d'), bg=SURFACE, fg=MUTED, font=('Consolas', 9)).pack(side='right')
        self.total = tk.Label(stats, text='—', bg=SURFACE, fg=TEXT, font=('Consolas', 27, 'bold'), anchor='w')
        self.total.pack(fill='x', pady=(3, 0))
        self.invest = tk.Label(stats, text='学习 / 编程 / 工作 —', bg=SURFACE, fg=MUTED, anchor='w', font=('Microsoft YaHei UI', 8))
        self.invest.pack(fill='x')
        labelrow = tk.Frame(frame, bg=BG)
        labelrow.pack(fill='x', pady=(11, 5))
        tk.Label(labelrow, text='时间分配', bg=BG, fg=TEXT, anchor='w', font=('Microsoft YaHei UI', 9, 'bold')).pack(side='left')
        self.button(labelrow, '详细报表 ↗', lambda: self.open('report')).pack(side='right')
        self.chart = tk.Canvas(frame, width=332, height=86, bg=BG, highlightthickness=0)
        self.chart.pack(fill='x')
        self.recent = tk.Label(frame, text='暂无记录', bg=BG, fg=MUTED, anchor='w', justify='left', height=1, font=('Microsoft YaHei UI', 8))
        self.recent.pack(fill='x', pady=(3, 4))
        timer = tk.Frame(frame, bg=SURFACE, padx=8, pady=6)
        timer.pack(fill='x')
        self.task = tk.Entry(timer, bg='#202429', fg=TEXT, insertbackground=TEXT, relief='flat', width=21)
        self.task.insert(0, '专注一件事')
        self.task.pack(side='left', ipady=4, fill='x', expand=True)
        self.timer_button = self.button(timer, '开始', self.toggle_timer, primary=True)
        self.timer_button.pack(side='right', padx=(8, 0))
        actions = tk.Frame(frame, bg=BG)
        actions.pack(fill='x', pady=(8, 3))
        self.pause_button = self.button(actions, '暂停记录', self.pause)
        self.pause_button.pack(side='left')
        self.button(actions, '历史线索', lambda: self.open('history')).pack(side='left', padx=5)
        self.button(actions, '设置', lambda: self.open('settings')).pack(side='right')
        self.footer = tk.Label(frame, text='关闭面板仍会记录 · 数据保存在本机', bg=BG, fg=MUTED, anchor='w', font=('Microsoft YaHei UI', 8))
        self.footer.pack(fill='x', pady=(3, 0))
        self.timer = {}
        self.root.after(50, self.refresh)
        self.root.after(100, self.pump)

    def button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, bg=ACCENT if primary else '#3c4147',
                         fg='#20312b' if primary else TEXT, activebackground='#48515a', activeforeground=TEXT,
                         relief='flat', bd=0, padx=8, pady=4, cursor='hand2', font=('Microsoft YaHei UI', 8), takefocus=True)

    def open(self, page):
        webbrowser.open(self.base + '/#' + page)

    def pin(self):
        self.topmost = not self.topmost
        self.root.attributes('-topmost', self.topmost)

    def submit(self, work):
        if self.busy: return
        self.busy = True
        def worker():
            try: self.queue.put(('ok', work()))
            except Exception as exc: self.queue.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def fetch(self):
        cfg = self.request('/api/config')
        day = self.request('/api/day?date=' + dt.date.today().isoformat())
        timer = self.request('/api/timer')
        return cfg, day, timer

    def refresh(self):
        self.submit(self.fetch)
        self.root.after(10000, self.refresh)

    def pump(self):
        try:
            state, data = self.queue.get_nowait()
            self.busy = False
            if state == 'ok': self.render(*data)
            else: self.status.configure(text='连接失败 · 请重开软件', fg='#f47788')
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def render(self, cfg, day, timer):
        self.cfg, self.timer = cfg, timer
        paused = cfg['settings'].get('paused', False)
        health = cfg.get('health', {})
        self.status.configure(text='已暂停' if paused else '● 离开电脑 · 后台记录中' if health.get('state') == 'idle' else '● 后台记录中', fg=MUTED if paused else ACCENT)
        self.total.configure(text=short_duration(day.get('total', 0)))
        totals = day.get('totals', {})
        self.invest.configure(text='学习 / 编程 / 工作  ' + short_duration(sum(totals.get(k, 0) for k in ('学习', '编程', '工作'))))
        self.chart.delete('all')
        items = sorted(totals.items(), key=lambda row: row[1], reverse=True)[:3]
        maximum = max([v for _, v in items] or [1]) or 1
        for i, (name, value) in enumerate(items):
            y = i * 29 + 8
            self.chart.create_text(0, y, text=name, fill=TEXT, anchor='w', font=('Microsoft YaHei UI', 8))
            self.chart.create_text(330, y, text=short_duration(value), fill=MUTED, anchor='e', font=('Consolas', 9))
            self.chart.create_rectangle(0, y + 10, 330, y + 13, fill='#424850', outline='')
            self.chart.create_rectangle(0, y + 10, max(2, 330 * value / maximum), y + 13, fill=cfg['colors'].get(name, ACCENT), outline='')
        if not items: self.chart.create_text(0, 20, text='后台开始记录后，这里会出现分布', fill=MUTED, anchor='w', font=('Microsoft YaHei UI', 8))
        recent = list(reversed(day.get('records', [])))[:1]
        self.recent.configure(text='\n'.join((row.get('app_purpose', {}).get('display_name') or row.get('note') or row.get('app') or '活动')[:25] + ' · ' + row['category'] for row in recent) or '暂无记录')
        pending = bool(timer.get('recovery'))
        self.timer_button.configure(text='继续' if pending else '结束' if timer.get('running') else '开始')
        if timer.get('running') or pending:
            entry = timer.get('entry') or timer.get('recovery', {}).get('entry') or {}
            self.task.configure(state='normal')
            self.task.delete(0, 'end'); self.task.insert(0, entry.get('note', '任务计时'))
            self.task.configure(state='disabled')
        else: self.task.configure(state='normal')
        self.pause_button.configure(text='恢复记录' if paused else '暂停记录')
        self.footer.configure(text='登录时静默记录 · 关闭面板仍会记录' if cfg.get('autostart_enabled') else '关闭面板仍会记录 · 可在设置开启登录启动')

    def pause(self):
        if not self.cfg: return
        enabled = not self.cfg['settings'].get('paused', False)
        def work():
            self.request('/api/settings', {'paused': enabled}, self.cfg['token'])
            return self.fetch()
        self.submit(work)

    def toggle_timer(self):
        if not self.cfg: return
        note = self.task.get().strip() or '专注一件事'
        def work():
            if self.timer.get('recovery'):
                self.request('/api/timer/resume', {'recovery_id': self.timer['recovery']['recovery_id']}, self.cfg['token'])
            elif self.timer.get('running'):
                self.request('/api/timer/stop', {}, self.cfg['token'])
            else:
                self.request('/api/timer/start', {'note': note, 'category': '工作'}, self.cfg['token'])
            return self.fetch()
        self.submit(work)


def show_panel(request, base):
    # One panel per user/port, independent of the collector's own lifecycle.
    handle = None
    if __import__('sys').platform == 'win32':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel.CreateMutexW(None, False, 'Local\\DayTracePanel-' + base.rsplit(':', 1)[-1])
        if handle and ctypes.get_last_error() == 183:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(handle)
            return
    try:
        panel = CompactPanel(request, base)
        panel.root.mainloop()
    finally:
        if handle:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(handle)
