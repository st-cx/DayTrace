"""Local Windows launcher: start once, run quietly, stop with a database flush."""
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ.get('DAYTRACE_HOME') or (Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'DayTrace'))
PORT = int(os.environ.get('DAYTRACE_PORT', '41789'))
BASE = f'http://127.0.0.1:{PORT}'

def request(path, data=None, token=None):
    headers = {}
    if token:
        headers['X-DayTrace-Key'] = token
    body = None
    if data is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(BASE + path, data=body, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=2) as response:
        return json.load(response)

def config():
    try:
        result = request('/api/config')
        if (result.get('app') == 'daytrace' or result.get('version') == '0.1.0') and 'tracking_supported' in result and 'colors' in result:
            return result
    except (OSError, ValueError):
        pass
    return None

def serve():
    os.environ['DAYTRACE_HOME'] = str(HOME)
    os.environ['DAYTRACE_PORT'] = str(PORT)
    import daytrace as app

    app.run_server(open_browser=False)

def show_panel():
    from daytrace_panel import show_panel as open_panel
    open_panel(request, BASE)


def start(background=False):
    if config() is None:
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(('127.0.0.1', PORT)) == 0:
                raise RuntimeError(f'端口 {PORT} 已被其他程序占用，请查看 DayTrace 日志。')
        HOME.mkdir(parents=True, exist_ok=True)
        with (HOME / 'launcher.log').open('a', encoding='utf-8') as log:
            process = subprocess.Popen(
                [sys.executable, 'serve'] if getattr(sys, 'frozen', False) else [sys.executable, '-u', str(Path(__file__).resolve()), 'serve'],
                cwd=str(ROOT), stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        for _ in range(100 if getattr(sys, 'frozen', False) else 40):
            if config() is not None:
                break
            if process.poll() is not None:
                raise RuntimeError('DayTrace 启动失败，请查看 %LOCALAPPDATA%\\DayTrace\\launcher.log。')
            time.sleep(0.2)
        else:
            raise RuntimeError('DayTrace 启动超时，请查看日志。')
    if not background:
        show_panel()
    print('DayTrace running at ' + BASE)

def stop():
    cfg = config()
    if cfg is None:
        print('DayTrace is already stopped')
        return
    request('/api/shutdown', {}, cfg['token'])
    for _ in range(40):
        if config() is None:
            print('DayTrace stopped')
            return
        time.sleep(0.2)
    raise RuntimeError('DayTrace 仍在退出，请稍后重试。')

def main():
    command = sys.argv[1] if len(sys.argv) > 1 else 'start'
    if command == 'serve':
        serve()
    elif command == 'start':
        start('--background' in sys.argv)
    elif command == 'stop':
        stop()
    elif command == 'status':
        cfg = config()
        print(json.dumps({'running': cfg is not None, 'settings': cfg['settings'] if cfg else None}, ensure_ascii=False))
    else:
        raise ValueError('Unknown command')

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        HOME.mkdir(parents=True, exist_ok=True)
        with (HOME / 'launcher.log').open('a', encoding='utf-8') as log:
            import traceback
            traceback.print_exc(file=log)
        if (getattr(sys, 'frozen', False) or Path(sys.executable).stem.lower() == 'pythonw') and 'serve' not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), 'DayTrace', 0x10)
        else:
            print(str(exc), file=sys.stderr)
        sys.exit(1)
