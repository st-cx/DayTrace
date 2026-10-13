"""Local HTTP interface and service lifecycle, shared by all launchers."""
import csv
import datetime as dt
import io
import json
import logging
import os
from pathlib import Path
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from daytrace_core.store import Store, DEFAULTS, timestamp, validate_settings
from daytrace_core.timeline import COLORS, valid_date, day_bounds, date_range, overlaps, reconcile, summarize
from daytrace_core.tracker import Tracker, IS_WIN, foreground, idle_for
from daytrace_core.windows import set_startup, startup_enabled

VERSION = '0.4.0'
APP_ROOT = Path(__file__).resolve().parent
WEB_ROOT = APP_ROOT / 'web'
DATA_DIR = Path(os.environ.get('DAYTRACE_HOME') or (Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'DayTrace'))
PORT = int(os.environ.get('DAYTRACE_PORT', '41789'))
STORE = TRACKER = SERVER = None


def csv_cell(value):
    value = str(value)
    return "'" + value if value and value[0] in '=+-@\t\r' else value


def report(a, b, first, last):
    timeline = reconcile(STORE.query(a, b), a, b)
    days = []
    date = dt.date.fromisoformat(first)
    while date <= dt.date.fromisoformat(last):
        da, db = day_bounds(date.isoformat())
        subset = [{**r, 'start': max(da, r['start']), 'end': min(db, r['end'])} for r in timeline if r['end'] > da and r['start'] < db]
        days.append({'date': date.isoformat(), **summarize(subset)})
        date += dt.timedelta(days=1)
    return {'from': first, 'to': last, 'days': days, **summarize(timeline)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send(self, status, data, content_type='application/json; charset=utf-8', attachment=None):
        if not isinstance(data, bytes):
            data = (json.dumps(data, ensure_ascii=False) if content_type.startswith('application/json') else data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if attachment:
            self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
        origin = self.headers.get('Origin', '')
        if self.path == '/api/browser' and origin.startswith(('chrome-extension://', 'moz-extension://')):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def host_allowed(self):
        return self.headers.get('Host', '') in (f'127.0.0.1:{PORT}', f'localhost:{PORT}')

    def origin_allowed(self):
        origin = self.headers.get('Origin', '')
        return not origin or origin in (f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}') or (self.path == '/api/browser' and origin.startswith(('chrome-extension://', 'moz-extension://')))

    def authorized(self, browser=False):
        supplied = self.headers.get('X-DayTrace-Key', '')
        expected = STORE.browser_token if browser else STORE.token
        return secrets.compare_digest(supplied, expected) or (browser and secrets.compare_digest(supplied, STORE.token))

    def do_OPTIONS(self):
        origin = self.headers.get('Origin', '')
        if not self.host_allowed() or self.path != '/api/browser' or not origin.startswith(('chrome-extension://', 'moz-extension://')):
            return self.send(403, {'error': 'forbidden'})
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-DayTrace-Key')
        self.send_header('Access-Control-Max-Age', '3600')
        self.end_headers()

    def do_GET(self):
        if not self.host_allowed() or not self.origin_allowed():
            return self.send(403, {'error': 'forbidden'})
        url = urlparse(self.path)
        query = parse_qs(url.query)
        def value(key, default=''):
            return query.get(key, [default])[0]
        try:
            if url.path in ('/', '/index.html', '/app.js', '/style.css'):
                filename = 'index.html' if url.path == '/' else url.path.lstrip('/')
                kind = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript; charset=utf-8', 'style.css': 'text/css; charset=utf-8'}[filename]
                return self.send(200, (WEB_ROOT / filename).read_bytes(), kind)
            if url.path == '/api/config':
                return self.send(200, {'app': 'daytrace', 'version': VERSION, 'settings': STORE.settings, 'rules': STORE.rules(), 'colors': COLORS,
                                       'token': STORE.token, 'browser_token': STORE.browser_token, 'tracking_supported': IS_WIN,
                                       'health': TRACKER.health(), 'autostart_enabled': startup_enabled(APP_ROOT)})
            if url.path == '/api/health':
                return self.send(200, TRACKER.health())
            if url.path == '/api/system-history':
                if not self.authorized():
                    return self.send(403, {'error': 'invalid token'})
                from daytrace_core.system_history import scan_history
                return self.send(200, scan_history(force=value('refresh') == '1'))
            if url.path == '/api/day':
                date = valid_date(value('date'))
                a, b = day_bounds(date)
                records = STORE.query(a, b)
                timeline = reconcile(records, a, b)
                visible = [{**row, 'app_purpose': STORE.app_purpose(row['app']), 'visible_start': max(a, row['start']), 'visible_end': min(b, row['end'])} for row in records]
                return self.send(200, {'date': date, 'records': visible, 'timeline': timeline,
                                       'goal_minutes': STORE.settings['daily_goal_minutes'], **summarize(timeline)})
            if url.path == '/api/timer':
                return self.send(200, STORE.timer_status())
            if url.path == '/api/projects':
                return self.send(200, {'projects': STORE.projects()})
            if url.path in ('/api/report', '/api/period'):
                if url.path == '/api/period':
                    kind = value('kind', 'week')
                    date = dt.date.fromisoformat(valid_date(value('date')))
                    if kind == 'week':
                        start = date - dt.timedelta(days=date.weekday())
                        end = start + dt.timedelta(days=6)
                    elif kind == 'month':
                        start = date.replace(day=1)
                        next_month = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
                        end = next_month - dt.timedelta(days=1)
                    else:
                        raise ValueError('不支持的统计范围')
                    first, last = start.isoformat(), end.isoformat()
                else:
                    first, last = valid_date(value('from')), valid_date(value('to', value('from')))
                    kind = 'range'
                first, last, a, b = date_range(first, last)
                return self.send(200, {'kind': kind, **report(a, b, first, last)})
            if url.path == '/api/records':
                first, last, a, b = date_range(value('from'), value('to', value('from')))
                records = STORE.query(a, b)
                word = value('q').strip().lower()
                def matches(row):
                    content = ' '.join(str(row.get(field, '')) for field in ('app', 'title', 'domain', 'note', 'project', 'tags')).lower()
                    return (not word or word in content) and all(not value(field) or row.get(field, '') == value(field) for field in ('category', 'project', 'source')) and (not value('tag') or value('tag') in row['tags'])
                selected = [row for row in records if matches(row)]
                effective = [row for row in reconcile(records, a, b) if matches(row)]
                page, size = int(value('page', '1')), int(value('page_size', '30'))
                if page < 1 or not 1 <= size <= 100:
                    raise ValueError('分页范围不正确')
                selected.reverse()
                visible = [{**row, 'app_purpose': STORE.app_purpose(row['app']), 'visible_start': max(a, row['start']), 'visible_end': min(b, row['end'])} for row in selected[(page - 1) * size:page * size]]
                return self.send(200, {'records': visible, 'page': page, 'page_size': size,
                                       'total_count': len(selected), 'total_seconds': sum(row['end'] - row['start'] for row in effective)})
            if url.path == '/api/backup':
                return self.send(200, STORE.export_backup(), attachment='DayTrace-backup.json')
            if url.path == '/api/export':
                first, last, a, b = date_range(value('from'), value('to', value('from')))
                mode = value('mode', 'effective')
                if mode not in ('effective', 'raw'):
                    raise ValueError('导出模式不正确')
                records = STORE.query(a, b)
                records = reconcile(records, a, b) if mode == 'effective' else [{**row, 'start': max(a, row['start']), 'end': min(b, row['end'])} for row in records]
                out = io.StringIO()
                writer = csv.writer(out)
                writer.writerow(['开始', '结束', '时长分钟', '应用', '窗口标题', '网站域名', '分类', '来源', '备注', '项目', '标签', '计费', '时薪'])
                for row in records:
                    writer.writerow([dt.datetime.fromtimestamp(row['start']).isoformat(sep=' ', timespec='microseconds'),
                                     dt.datetime.fromtimestamp(row['end']).isoformat(sep=' ', timespec='microseconds'),
                                     round((row['end'] - row['start']) / 60, 6), *[csv_cell(row[key]) for key in ('app', 'title', 'domain', 'category', 'source', 'note', 'project')],
                                     csv_cell('|'.join(row['tags'])), int(row['billable']), row['hourly_rate']])
                return self.send(200, ('\ufeff' + out.getvalue()).encode('utf-8'), 'text/csv; charset=utf-8', f'DayTrace-{mode}.csv')
            self.send(404, {'error': 'not found'})
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            self.send(400, {'error': str(exc)})
        except Exception:
            logging.exception('DayTrace GET failed')
            self.send(500, {'error': '处理失败，请查看本机日志'})

    def body(self):
        length = int(self.headers.get('Content-Length', '0'))
        maximum = 8_000_000 if self.path == '/api/import' else 20000
        if not 0 < length <= maximum:
            raise ValueError('请求内容为空或过大')
        self.connection.settimeout(10)
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError('请求内容应为对象')
        return data

    def do_POST(self):
        browser = self.path == '/api/browser'
        if not self.host_allowed() or not self.origin_allowed() or not self.authorized(browser=browser):
            return self.send(403, {'error': 'invalid token or origin'})
        try:
            data = self.body()
            if self.path == '/api/manual':
                metadata = {key: data[key] for key in ('project', 'tags', 'billable', 'hourly_rate') if key in data}
                return self.send(200, {'id': STORE.add_manual(timestamp(data['start']), timestamp(data['end']), data['category'], data.get('note', ''), **metadata)})
            if self.path == '/api/settings':
                old = dict(STORE.settings)
                new = validate_settings(data, old)
                changed_startup = new['autostart'] != startup_enabled(APP_ROOT)
                if changed_startup:
                    set_startup(APP_ROOT, new['autostart'])
                try:
                    result = STORE.update_settings(data)
                except Exception:
                    if changed_startup:
                        set_startup(APP_ROOT, old['autostart'])
                    raise
                return self.send(200, {'settings': result})
            if self.path == '/api/rule':
                STORE.add_rule(data['pattern'], data['category'], data.get('target', 'any'), data.get('project', ''), data.get('tags', []))
                return self.send(200, {'ok': True})
            if self.path == '/api/rules/apply':
                _, _, a, b = date_range(data.get('from'), data.get('to', data.get('from')))
                if not isinstance(data.get('dry_run', False), bool):
                    raise ValueError('预览标记应为开关值')
                return self.send(200, STORE.apply_rules(a, b, data.get('dry_run', False)))
            if self.path == '/api/projects':
                return self.send(200, STORE.save_project(data))
            if self.path == '/api/timer/start':
                return self.send(200, STORE.start_timer(data))
            if self.path == '/api/timer/stop':
                return self.send(200, STORE.stop_timer())
            if self.path == '/api/timer/resume':
                return self.send(200, STORE.resume_timer(data['recovery_id']))
            if self.path == '/api/timer/dismiss-recovery':
                return self.send(200, STORE.dismiss_recovery(data['recovery_id']))
            if self.path == '/api/import':
                backup = STORE.create_backup()
                return self.send(200, {**STORE.import_backup(data), 'backup_path': backup})
            if self.path == '/api/backup/create':
                return self.send(200, {'path': STORE.create_backup()})
            if self.path == '/api/browser':
                if not STORE.settings['browser_enabled']:
                    return self.send(403, {'error': 'browser tracking disabled'})
                accepted = TRACKER.set_browser(data.get('domain', ''), data.get('browser', 'chrome.exe'), data.get('focused', True), data.get('private', False), data.get('client_id', ''))
                return self.send(200 if accepted else 400, {'ok': True} if accepted else {'error': 'invalid domain or browser'})
            if self.path == '/api/shutdown':
                if not SERVER:
                    raise ValueError('服务未启动')
                self.send(200, {'ok': True})
                threading.Thread(target=SERVER.shutdown, daemon=True).start()
                return
            self.send(404, {'error': 'not found'})
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            self.send(400, {'error': str(exc)})
        except Exception:
            logging.exception('DayTrace POST failed')
            self.send(500, {'error': '处理失败，请查看本机日志'})

    def do_PUT(self):
        if not self.host_allowed() or not self.origin_allowed() or not self.authorized():
            return self.send(403, {'error': 'invalid token or origin'})
        try:
            parts = urlparse(self.path).path.split('/')
            if len(parts) == 4 and parts[:3] == ['', 'api', 'record']:
                return self.send(200, {'record': STORE.update_record(int(parts[3]), self.body())})
            self.send(404, {'error': 'not found'})
        except (ValueError, TypeError, KeyError) as exc:
            self.send(400, {'error': str(exc)})

    def do_DELETE(self):
        if not self.host_allowed() or not self.origin_allowed() or not self.authorized():
            return self.send(403, {'error': 'invalid token or origin'})
        try:
            parts = urlparse(self.path).path.split('/')
            if len(parts) == 4 and parts[:2] == ['', 'api']:
                rid = int(parts[3])
                if parts[2] == 'record':
                    return self.send(200, {'removed': STORE.remove(rid)})
                if parts[2] == 'rule':
                    STORE.remove_rule(rid)
                    return self.send(200, {'ok': True})
                if parts[2] == 'project':
                    STORE.archive_project(rid)
                    return self.send(200, {'ok': True})
            self.send(404, {'error': 'not found'})
        except (ValueError, TypeError) as exc:
            self.send(400, {'error': str(exc)})


def run_server(open_browser=False):
    global STORE, TRACKER, SERVER
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SERVER = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    SERVER.daemon_threads = True
    try:
        STORE = Store(DATA_DIR / 'daytrace.sqlite3')
        if not STORE.settings_path.exists():
            STORE.update_settings({})
        TRACKER = Tracker(STORE)
        TRACKER.start()
        if open_browser:
            webbrowser.open(f'http://127.0.0.1:{PORT}')
        print(f'DayTrace v{VERSION} started', flush=True)
        SERVER.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if TRACKER:
            TRACKER.stop()
            TRACKER.join(timeout=7)
        SERVER.server_close()
        if STORE:
            STORE.db.close()
        print('DayTrace stopped; database closed', flush=True)


def main():
    # Frozen executables receive their own serve/stop commands; source entry
    # points use the same launcher rather than starting another collector.
    from manage_daytrace import main as launcher_main
    launcher_main()
