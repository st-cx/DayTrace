import copy
import datetime as dt
import json
import math
from pathlib import Path
import secrets
import sqlite3
import threading
import time

from .timeline import COLORS, reconcile
from .csv_import import parse_csv
from .app_purpose import describe_app, normalize_app

DEFAULTS = {
    'record_titles': False, 'paused': False, 'idle_seconds': 180,
    'excluded_apps': ['bitwarden.exe', 'keepass.exe', '1password.exe', 'credentialuibroker.exe'],
    'excluded_titles': ['无痕', '隐身', 'incognito', 'inprivate', '银行', 'banking', 'password'],
    'excluded_domains': ['accounts.google.com', 'login.live.com', 'paypal.com'],
    'browser_enabled': False, 'daily_goal_minutes': 120, 'autostart': False,
}
RECORD_FIELDS = ('start', 'end', 'app', 'title', 'domain', 'category', 'source', 'note',
                 'project', 'tags', 'billable', 'hourly_rate')


def number(value, minimum=0, maximum=1e7):
    if isinstance(value, bool):
        raise ValueError('数值格式不正确')
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError('数值格式不正确') from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError('数值超出允许范围')
    return value


def text(value, maximum, strip=True):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f'文本最多{maximum}字')
    return value.strip() if strip else value


def tags(value):
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError('标签应为列表，最多20个')
    return list(dict.fromkeys(text(item, 40) for item in value if item))


def timestamp(value):
    if isinstance(value, str):
        try:
            value = dt.datetime.fromisoformat(value).timestamp()
        except (ValueError, OverflowError, OSError):
            raise ValueError('时间格式不正确') from None
    return number(value, -2208988800, 4102444800)


def validate_settings(changes, current):
    if not isinstance(changes, dict):
        raise ValueError('设置应为对象')
    result = copy.deepcopy(current)
    for key, value in changes.items():
        if key not in DEFAULTS:
            continue
        default = DEFAULTS[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ValueError(f'{key} 应为开关值')
            result[key] = value
        elif isinstance(default, int):
            low, high = (20, 1800) if key == 'idle_seconds' else (0, 1440)
            parsed = number(value, low, high)
            if parsed != int(parsed):
                raise ValueError('分钟或秒数应为整数')
            result[key] = int(parsed)
        elif isinstance(default, list):
            if not isinstance(value, list) or len(value) > 100:
                raise ValueError(f'{key} 应为不超过100项的列表')
            result[key] = [text(item, 250).lower() for item in value if item]
    return result


def validate_record(data, old=None):
    if not isinstance(data, dict):
        raise ValueError('记录应为对象')
    result = {'app': '', 'title': '', 'domain': '', 'category': '其他', 'source': 'manual',
              'note': '', 'project': '', 'tags': [], 'billable': False, 'hourly_rate': 0}
    if old:
        result.update({key: old[key] for key in RECORD_FIELDS})
    result.update({key: value for key, value in data.items() if key in RECORD_FIELDS})
    result['start'], result['end'] = timestamp(result.get('start')), timestamp(result.get('end'))
    maximum = 366 * 86400 if result['source'] == 'auto' else 48 * 3600
    if not 0 < result['end'] - result['start'] <= maximum:
        raise ValueError('结束时间必须晚于开始时间；手动活动最多48小时')
    if result['category'] not in COLORS or result['source'] not in ('auto', 'manual', 'timer'):
        raise ValueError('记录分类或来源不正确')
    for field, maximum in [('app', 250), ('title', 1024), ('domain', 250), ('note', 500), ('project', 100)]:
        result[field] = text(result[field], maximum, strip=field not in ('title', 'note'))
    result['tags'] = tags(result['tags'])
    if not isinstance(result['billable'], bool):
        raise ValueError('计费标记应为开关值')
    result['hourly_rate'] = number(result['hourly_rate'], 0, 1e6)
    return result


def row_dict(row):
    result = dict(row)
    try:
        result['tags'] = json.loads(result.get('tags') or '[]')
    except ValueError:
        result['tags'] = []
    result['billable'] = bool(result.get('billable', False))
    return result


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.settings_mutex = threading.RLock()
        self.on_settings_change = None
        self.last_id = self.last_key = None
        self.timer = None
        self.recovery = None
        self.timer_last_reason = ''
        with self.lock:
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA busy_timeout=10000')
            self.db.execute('CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY, start REAL NOT NULL, end REAL NOT NULL, app TEXT NOT NULL DEFAULT "", title TEXT NOT NULL DEFAULT "", domain TEXT NOT NULL DEFAULT "", category TEXT NOT NULL, source TEXT NOT NULL, note TEXT NOT NULL DEFAULT "")')
            columns = {row['name'] for row in self.db.execute('PRAGMA table_info(records)')}
            for name, definition in [('project', "TEXT NOT NULL DEFAULT ''"), ('tags', "TEXT NOT NULL DEFAULT '[]'"), ('billable', 'INTEGER NOT NULL DEFAULT 0'), ('hourly_rate', 'REAL NOT NULL DEFAULT 0')]:
                if name not in columns:
                    self.db.execute(f'ALTER TABLE records ADD COLUMN {name} {definition}')
            self.db.execute('CREATE INDEX IF NOT EXISTS ix_records_time ON records(start,end)')
            self.db.execute('CREATE TABLE IF NOT EXISTS rules (id INTEGER PRIMARY KEY, pattern TEXT NOT NULL, category TEXT NOT NULL)')
            columns = {row['name'] for row in self.db.execute('PRAGMA table_info(rules)')}
            for name, definition in [('target', "TEXT NOT NULL DEFAULT 'any'"), ('project', "TEXT NOT NULL DEFAULT ''"), ('tags', "TEXT NOT NULL DEFAULT '[]'")]:
                if name not in columns:
                    self.db.execute(f'ALTER TABLE rules ADD COLUMN {name} {definition}')
            self.db.execute('CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, color TEXT NOT NULL, hourly_rate REAL NOT NULL DEFAULT 0, budget_minutes INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0)')
            self.db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS app_categories (app TEXT PRIMARY KEY, category TEXT NOT NULL)')
            pairing = self.db.execute('SELECT value FROM meta WHERE key="browser_token"').fetchone()
            self.browser_token = pairing['value'] if pairing else secrets.token_urlsafe(32)
            if not pairing:
                self.db.execute('INSERT INTO meta VALUES("browser_token",?)', (self.browser_token,))
            active = self.db.execute('SELECT value FROM meta WHERE key="active_timer"').fetchone()
            if active and active['value'] != 'null':
                try:
                    self.recovery = self._make_recovery(json.loads(active['value']), 'recovered')
                except (ValueError, TypeError, KeyError):
                    self.recovery = None
                self._persist_recovery()
                self.db.execute('UPDATE meta SET value="null" WHERE key="active_timer"')
            else:
                candidate = self.db.execute('SELECT value FROM meta WHERE key="recovery_timer"').fetchone()
                if candidate and candidate['value'] != 'null':
                    try:
                        self.recovery = self._validate_recovery(json.loads(candidate['value']))
                    except (ValueError, TypeError, KeyError):
                        self.recovery = None
                    self._persist_recovery()
            if self.recovery:
                self.timer_last_reason = self.recovery['reason']
            self.db.execute('PRAGMA user_version=3')
            self.db.commit()
        self.settings_path = self.path.with_name('settings.json')
        self.settings = self._load_settings()
        self.token = secrets.token_urlsafe(32)
        self._rules_cache = None

    def _load_settings(self):
        result = copy.deepcopy(DEFAULTS)
        if self.settings_path.exists():
            try:
                data = json.loads(self.settings_path.read_text(encoding='utf-8-sig'))
                if isinstance(data, dict):
                    for key, value in data.items():
                        try:
                            result = validate_settings({key: value}, result)
                        except ValueError:
                            continue
            except (OSError, ValueError):
                pass
        return result

    def _commit_settings(self, settings):
        with self.lock:
            temporary = self.settings_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.settings_path)
            self.settings = settings
        return copy.deepcopy(settings)

    def update_settings(self, changes):
        with self.settings_mutex:
            settings = validate_settings(changes, self.settings)
            if self.on_settings_change:
                return self.on_settings_change(settings)
            return self._commit_settings(settings)

    def rules(self):
        with self.lock:
            if self._rules_cache is None:
                self._rules_cache = []
                for row in self.db.execute('SELECT * FROM rules ORDER BY id DESC'):
                    record = dict(row)
                    record['tags'] = json.loads(record['tags'])
                    self._rules_cache.append(record)
            return copy.deepcopy(self._rules_cache)

    def classify_details(self, app, title, domain):
        fields = {'app': app.lower(), 'title': title.lower(), 'domain': domain.lower(), 'any': f'{app} {title} {domain}'.lower()}
        for rule in self.rules():
            if rule['pattern'] in fields[rule['target']]:
                return {key: rule[key] for key in ('category', 'project', 'tags')}
        with self.lock:
            remembered = self.db.execute('SELECT category FROM app_categories WHERE app=?', (normalize_app(app),)).fetchone()
        if remembered:
            return {'category': remembered['category'], 'project': '', 'tags': []}
        return {'category': describe_app(app)['category'], 'project': '', 'tags': []}

    def classify(self, app, title, domain):
        return self.classify_details(app, title, domain)['category']

    def app_purpose(self, app):
        purpose = describe_app(app)
        with self.lock:
            row = self.db.execute('SELECT category FROM app_categories WHERE app=?', (purpose['app_name'],)).fetchone()
        return {**purpose, 'remembered_category': row['category'] if row else None}

    def _insert(self, record):
        values = [json.dumps(record[key], ensure_ascii=False) if key == 'tags' else int(record[key]) if key == 'billable' else record[key] for key in RECORD_FIELDS]
        cursor = self.db.execute(f'INSERT INTO records({",".join(RECORD_FIELDS)}) VALUES({",".join("?" for _ in values)})', values)
        return cursor.lastrowid

    def add_interval(self, start, end, state):
        if not 0 < end - start <= 20:
            return
        record = validate_record({'start': start, 'end': end, 'source': 'auto', **state})
        key = tuple(json.dumps(record[key], ensure_ascii=False, sort_keys=True) for key in RECORD_FIELDS if key not in ('start', 'end'))
        with self.lock:
            if self.last_id and key == self.last_key:
                row = self.db.execute('SELECT end FROM records WHERE id=?', (self.last_id,)).fetchone()
                if row and abs(row['end'] - start) < 1:
                    self.db.execute('UPDATE records SET end=? WHERE id=?', (end, self.last_id))
                    self.db.commit()
                    return
            self.last_id = self._insert(record)
            self.last_key = key
            self.db.commit()

    def reset_interval(self):
        with self.lock:
            self.last_id = self.last_key = None

    def add_manual(self, start, end, category, note, **metadata):
        record = validate_record({'start': start, 'end': end, 'category': category, 'note': note, **metadata, 'source': 'manual'})
        with self.lock:
            rid = self._insert(record)
            self._ensure_project(record['project'])
            self.db.commit()
            return rid

    def query(self, a, b):
        with self.lock:
            return [row_dict(row) for row in self.db.execute('SELECT * FROM records WHERE end>? AND start<? ORDER BY start,id', (a, b))]

    def update_record(self, rid, changes):
        with self.lock:
            if not isinstance(changes, dict) or not isinstance(changes.get('remember_app', False), bool):
                raise ValueError('记住应用归类应为开关值')
            row = self.db.execute('SELECT * FROM records WHERE id=?', (rid,)).fetchone()
            if row is None:
                raise ValueError('记录不存在')
            if self.timer and self.timer['record_id'] == rid:
                raise ValueError('请先停止计时，再编辑这条记录')
            record = validate_record({**changes, 'source': 'manual'}, row_dict(row))
            remember = changes.get('remember_app', False)
            if remember:
                app_name = normalize_app(row['app'])
                if row['source'] != 'auto' or not app_name.endswith('.exe') or normalize_app(record['app']) != app_name:
                    raise ValueError('只能记住原自动记录的应用，请勿修改应用名')
            # The historical correction and future app mapping commit or roll back together.
            with self.db:
                self.db.execute(f'UPDATE records SET {",".join(key+"=?" for key in RECORD_FIELDS)} WHERE id=?',
                                [json.dumps(record[key], ensure_ascii=False) if key == 'tags' else record[key] for key in RECORD_FIELDS] + [rid])
                self._ensure_project(record['project'])
                if remember:
                    self.db.execute('INSERT INTO app_categories(app,category) VALUES(?,?) ON CONFLICT(app) DO UPDATE SET category=excluded.category',
                                    (app_name, record['category']))
            self.reset_interval()
            return {**record, 'id': rid}

    def remove(self, rid):
        with self.lock:
            if self.timer and self.timer['record_id'] == rid:
                raise ValueError('请先停止计时，再删除这条记录')
            cursor = self.db.execute('DELETE FROM records WHERE id=?', (rid,))
            self.db.commit()
            self.reset_interval()
            return cursor.rowcount

    def add_rule(self, pattern, category, target='any', project='', tags=None):
        pattern = text(pattern, 100).lower()
        if not pattern or category not in COLORS or target not in ('any', 'app', 'title', 'domain'):
            raise ValueError('规则内容、分类或匹配类型不正确')
        project = text(project, 100)
        normalized_tags = globals()['tags'](tags or [])
        with self.lock:
            self.db.execute('INSERT INTO rules(pattern,category,target,project,tags) VALUES(?,?,?,?,?)',
                            (pattern, category, target, project, json.dumps(normalized_tags, ensure_ascii=False)))
            self._ensure_project(project)
            self.db.commit()
            self._rules_cache = None

    def remove_rule(self, rid):
        with self.lock:
            self.db.execute('DELETE FROM rules WHERE id=?', (rid,))
            self.db.commit()
            self._rules_cache = None

    def apply_rules(self, a, b, dry_run=False):
        changed = []
        with self.lock:
            for record in self.query(a, b):
                if record['source'] != 'auto':
                    continue
                details = self.classify_details(record['app'], record['title'], record['domain'])
                if any(record[key] != details[key] for key in details):
                    changed.append((details['category'], details['project'], json.dumps(details['tags'], ensure_ascii=False), record['id']))
            if not dry_run:
                self.db.executemany('UPDATE records SET category=?, project=?, tags=? WHERE id=?', changed)
                self.db.commit()
                self.reset_interval()
        return {'changed': len(changed), 'dry_run': dry_run}

    def _ensure_project(self, name):
        if name:
            self.db.execute('INSERT OR IGNORE INTO projects(name,color) VALUES(?,?)', (name, '#238d81'))

    def projects(self):
        with self.lock:
            return [{**dict(row), 'archived': bool(row['archived'])} for row in self.db.execute('SELECT * FROM projects ORDER BY archived,name')]

    def save_project(self, data):
        import re
        name = text(data.get('name', ''), 100)
        color = data.get('color', '#238d81')
        if not name or not isinstance(color, str) or not re.fullmatch(r'#[a-fA-F0-9]{6}', color):
            raise ValueError('项目名称和颜色不正确')
        rate = number(data.get('hourly_rate', 0), 0, 1e6)
        budget = int(number(data.get('budget_minutes', 0), 0, 1e7))
        archived = data.get('archived', False)
        if not isinstance(archived, bool):
            raise ValueError('归档标记格式不正确')
        with self.lock:
            try:
                if data.get('id'):
                    old = self.db.execute('SELECT name FROM projects WHERE id=?', (int(data['id']),)).fetchone()
                    if not old:
                        raise ValueError('项目不存在')
                    self.db.execute('UPDATE projects SET name=?,color=?,hourly_rate=?,budget_minutes=?,archived=? WHERE id=?', (name, color, rate, budget, int(archived), int(data['id'])))
                    self.db.execute('UPDATE records SET project=? WHERE project=?', (name, old['name']))
                    self.db.execute('UPDATE rules SET project=? WHERE project=?', (name, old['name']))
                    self._rules_cache = None
                    if self.timer and self.timer['record']['project'] == old['name']:
                        self.timer['record']['project'] = name
                        self._persist_timer()
                else:
                    self.db.execute('INSERT INTO projects(name,color,hourly_rate,budget_minutes,archived) VALUES(?,?,?,?,?)', (name, color, rate, budget, int(archived)))
                self.db.commit()
            except sqlite3.IntegrityError:
                self.db.rollback()
                raise ValueError('同名项目已存在') from None
        return {'ok': True}

    def archive_project(self, rid):
        with self.lock:
            self.db.execute('UPDATE projects SET archived=1 WHERE id=?', (rid,))
            self.db.commit()

    def _persist_timer(self):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES("active_timer",?)', (json.dumps(self.timer, ensure_ascii=False),))

    def _make_recovery(self, timer, reason):
        if not isinstance(timer, dict) or type(timer.get('record_id')) is not int or not 0 < timer['record_id'] <= 2 ** 63 - 1:
            return None
        row = self.db.execute('SELECT * FROM records WHERE id=?', (timer['record_id'],)).fetchone()
        if row is None:
            return None
        record = row_dict(row)
        snapshot = timer.get('record')
        if not isinstance(snapshot, dict) or any(snapshot.get(key) != record[key] for key in RECORD_FIELDS):
            return None
        if timestamp(timer.get('last')) != record['end']:
            return None
        focus = timer.get('focus_minutes')
        expected_deadline = record['start'] + (focus * 60 if type(focus) is int and focus else 48 * 3600)
        if timestamp(timer.get('deadline')) != expected_deadline:
            return None
        return self._validate_recovery({'recovery_id': secrets.token_urlsafe(18),
                                       'entry': {**record, 'focus_minutes': focus},
                                       'stopped_at': record['end'], 'reason': reason})

    def _validate_recovery(self, candidate):
        if not isinstance(candidate, dict) or not isinstance(candidate.get('entry'), dict):
            return None
        key, entry = candidate.get('recovery_id'), candidate['entry']
        if not isinstance(key, str) or not key.isascii() or not 10 <= len(key) <= 128:
            return None
        if type(entry.get('id')) is not int or not 0 < entry['id'] <= 2 ** 63 - 1 or type(entry.get('focus_minutes')) is not int or not 0 <= entry['focus_minutes'] <= 180:
            return None
        if candidate.get('reason') not in ('recovered', 'app_stopped', 'shutdown', 'sleep', 'clock_changed'):
            return None
        row = self.db.execute('SELECT * FROM records WHERE id=?', (entry['id'],)).fetchone()
        if row is None:
            return None
        record = row_dict(row)
        if record['source'] != 'timer' or any(entry.get(field) != record[field] for field in RECORD_FIELDS):
            return None
        # A task stopped before its first tick still has recoverable context.
        if record['end'] < record['start'] or timestamp(candidate.get('stopped_at')) != record['end']:
            return None
        validate_record({**record, 'end': record['end'] if record['end'] > record['start'] else record['start'] + 0.001})
        return {'recovery_id': key, 'entry': {**record, 'focus_minutes': entry['focus_minutes']},
                'stopped_at': record['end'], 'reason': candidate['reason']}

    def _current_recovery(self):
        if self.recovery is not None:
            try:
                validated = self._validate_recovery(self.recovery)
            except (ValueError, TypeError, KeyError):
                validated = None
            if validated is None:
                self.recovery = None
                self._persist_recovery()
                self.db.commit()
            else:
                self.recovery = validated
        return self.recovery

    def _persist_recovery(self):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES("recovery_timer",?)', (json.dumps(self.recovery, ensure_ascii=False),))

    def start_timer(self, data, *, recovery_id=None):
        now = time.time()
        focus = int(number(data.get('focus_minutes', 0), 0, 180))
        record = validate_record({'start': now, 'end': now + 0.001, 'category': data.get('category', '学习'),
                                  'note': data.get('note', ''), 'project': data.get('project', ''), 'tags': data.get('tags', []),
                                  'billable': data.get('billable', False), 'hourly_rate': data.get('hourly_rate', 0), 'source': 'timer'})
        with self.lock:
            if self.timer:
                raise ValueError('已有任务在计时，请先停止')
            self._current_recovery()
            if self.recovery and recovery_id != self.recovery['recovery_id']:
                raise ValueError('请先选择继续上次任务，或放弃继续')
            if recovery_id is not None and not self.recovery:
                raise ValueError('上次任务已处理，请刷新页面')
            previous_recovery, previous_reason = self.recovery, self.timer_last_reason
            try:
                record['end'] = now
                rid = self._insert(record)
                self.timer = {'record_id': rid, 'record': record, 'last': now, 'deadline': now + (focus * 60 if focus else 48 * 3600), 'focus_minutes': focus}
                self.timer_last_reason = ''
                self.recovery = None
                self._ensure_project(record['project'])
                self._persist_timer()
                self._persist_recovery()
                self.db.commit()
            except Exception:
                self.db.rollback()
                self.timer = None
                self.recovery, self.timer_last_reason = previous_recovery, previous_reason
                raise
        return self.timer_status(now)

    def resume_timer(self, recovery_id):
        with self.lock:
            self._current_recovery()
            if self.timer or not self.recovery or recovery_id != self.recovery['recovery_id']:
                raise ValueError('上次任务已处理，请刷新页面')
            entry = self.recovery['entry']
            data = {key: entry[key] for key in ('category', 'note', 'project', 'tags', 'billable', 'hourly_rate', 'focus_minutes')}
            return self.start_timer(data, recovery_id=recovery_id)

    def dismiss_recovery(self, recovery_id):
        with self.lock:
            self._current_recovery()
            if self.timer or not self.recovery or recovery_id != self.recovery['recovery_id']:
                raise ValueError('上次任务已处理，请刷新页面')
            previous = self.recovery
            try:
                self.recovery = None
                self._persist_recovery()
                self.db.commit()
            except Exception:
                self.db.rollback()
                self.recovery = previous
                raise
            self.timer_last_reason = 'recovery_dismissed'
            return self.timer_status()

    def checkpoint_timer(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if not self.timer:
                return
            gap = now - self.timer['last']
            if gap < 0 or gap > 20:
                self._finish_timer('sleep' if gap > 20 else 'clock_changed')
                return
            end = min(now, self.timer['deadline'])
            self.db.execute('UPDATE records SET end=? WHERE id=?', (end, self.timer['record_id']))
            self.timer['record']['end'] = end
            self.timer['last'] = end
            self._persist_timer()
            self.db.commit()
            if now >= self.timer['deadline']:
                self._finish_timer('focus_completed' if self.timer['focus_minutes'] else 'limit_reached')

    def _finish_timer(self, reason):
        preserve = reason in ('app_stopped', 'shutdown', 'sleep', 'clock_changed')
        if preserve and self.timer:
            self.recovery = self._make_recovery(self.timer, reason)
        if not preserve and self.timer and self.timer['record']['end'] <= self.timer['record']['start']:
            self.db.execute('DELETE FROM records WHERE id=?', (self.timer['record_id'],))
        self.timer = None
        self.timer_last_reason = reason
        self._persist_timer()
        self._persist_recovery()
        self.db.commit()

    def stop_timer(self, now=None, reason='stopped'):
        with self.lock:
            if not self.timer:
                return self.timer_status(now)
            self.checkpoint_timer(now)
            if self.timer:
                self._finish_timer(reason)
            elif reason == 'stopped' and self.recovery:
                # An explicit finish wins over gap detection in this request.
                self.dismiss_recovery(self.recovery['recovery_id'])
                self.timer_last_reason = 'stopped'
            return self.timer_status(now)

    def timer_status(self, now=None):
        with self.lock:
            if not self.timer:
                self._current_recovery()
                return {'running': False, 'entry': None, 'elapsed': 0, 'focus_remaining': None,
                        'last_reason': self.timer_last_reason, 'recovery': copy.deepcopy(self.recovery)}
            now = time.time() if now is None else now
            end = min(max(self.timer['last'], now), self.timer['last'] + 5, self.timer['deadline'])
            return {'running': True, 'entry': {**self.timer['record'], 'id': self.timer['record_id'], 'focus_minutes': self.timer['focus_minutes']},
                    'elapsed': max(0, end - self.timer['record']['start']),
                    'focus_remaining': max(0, self.timer['deadline'] - now) if self.timer['focus_minutes'] else None,
                    'last_reason': '', 'recovery': None}

    def export_backup(self):
        with self.lock:
            return {'format': 'daytrace-backup', 'schema_version': 3, 'created_at': dt.datetime.now().isoformat(timespec='seconds'),
                    'records': [row_dict(row) for row in self.db.execute('SELECT * FROM records WHERE end>start ORDER BY start,id')],
                    'rules': list(reversed(self.rules())), 'projects': self.projects(), 'settings': copy.deepcopy(self.settings),
                    'app_categories': [dict(row) for row in self.db.execute('SELECT app,category FROM app_categories ORDER BY app')]}

    def create_backup(self):
        directory = self.path.parent / 'backups'
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / ('DayTrace-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(2) + '.json')
        target.write_text(json.dumps(self.export_backup(), ensure_ascii=False, indent=2), encoding='utf-8')
        return str(target)

    def _csv_records(self, content, options=None):
        return parse_csv(content, options)

    def import_backup(self, data):
        if not isinstance(data, dict):
            raise ValueError('导入内容应为备份对象')
        if 'csv' in data:
            data = {'format': 'daytrace-backup', 'records': self._csv_records(data['csv'], data.get('csv_options'))}
        if data.get('format') != 'daytrace-backup' or not isinstance(data.get('records'), list) or len(data['records']) > 50000:
            raise ValueError('请选择DayTrace JSON备份或CSV文件（最多50000条）')
        records = [validate_record(row) for row in data['records']]
        project_data = data.get('projects', [])
        rule_data = data.get('rules', [])
        app_data = data.get('app_categories', [])
        if not isinstance(app_data, list) or len(app_data) > 5000:
            raise ValueError('应用归类记忆应为列表，最多5000项')
        for entry in app_data:
            if not isinstance(entry, dict):
                raise ValueError('应用归类记忆格式不正确')
            app_name = text(entry.get('app', ''), 250).casefold()
            if not app_name.endswith('.exe') or normalize_app(app_name) != app_name or entry.get('category') not in COLORS:
                raise ValueError('应用归类记忆需包含准确的应用名与有效分类')
        if not isinstance(project_data, list) or len(project_data) > 1000 or not isinstance(rule_data, list) or len(rule_data) > 1000:
            raise ValueError('项目或规则数量过多')
        # Validate the full file before making any change; snapshots protect rollback.
        for project in project_data:
            if not isinstance(project, dict) or not text(project.get('name', ''), 100):
                raise ValueError('备份项目格式不正确')
            number(project.get('hourly_rate', 0), 0, 1e6)
            number(project.get('budget_minutes', 0), 0, 1e7)
        for rule in rule_data:
            if not isinstance(rule, dict) or not text(rule.get('pattern', ''), 100) or rule.get('category') not in COLORS or rule.get('target', 'any') not in ('any', 'app', 'title', 'domain'):
                raise ValueError('备份规则格式不正确')
            text(rule.get('project', ''), 100)
            tags(rule.get('tags', []))
        imported = skipped = 0
        with self.lock:
            existing = {json.dumps({key: row_dict(row)[key] for key in RECORD_FIELDS}, ensure_ascii=False, sort_keys=True) for row in self.db.execute('SELECT * FROM records')}
            try:
                self.db.execute('BEGIN IMMEDIATE')
                for project in project_data:
                    self.db.execute('INSERT OR IGNORE INTO projects(name,color,hourly_rate,budget_minutes,archived) VALUES(?,?,?,?,?)',
                                    (project['name'], project.get('color', '#238d81'), project.get('hourly_rate', 0), int(project.get('budget_minutes', 0)), int(bool(project.get('archived', False)))))
                for record in records:
                    signature = json.dumps(record, ensure_ascii=False, sort_keys=True)
                    if signature in existing:
                        skipped += 1
                        continue
                    self._insert(record)
                    self._ensure_project(record['project'])
                    existing.add(signature)
                    imported += 1
                existing_rules = {(row['pattern'], row['category'], row['target'], row['project'], row['tags']) for row in self.db.execute('SELECT * FROM rules')}
                for rule in rule_data:
                    entry = (rule['pattern'].lower(), rule['category'], rule.get('target', 'any'), rule.get('project', ''), json.dumps(rule.get('tags', []), ensure_ascii=False))
                    if entry not in existing_rules:
                        self.db.execute('INSERT INTO rules(pattern,category,target,project,tags) VALUES(?,?,?,?,?)', entry)
                        existing_rules.add(entry)
                for entry in app_data:
                    # Import fills gaps; a user's current app correction takes precedence.
                    self.db.execute('INSERT OR IGNORE INTO app_categories(app,category) VALUES(?,?)', (entry['app'].strip().casefold(), entry['category']))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
            self._rules_cache = None
            self.reset_interval()
        return {'imported': imported, 'skipped': skipped, 'settings_restored': False}
