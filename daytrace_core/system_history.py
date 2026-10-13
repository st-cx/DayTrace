"""Read-only OS clues. Never invent foreground time or write tracking records."""
import base64
import datetime as dt
import json
import math
import ntpath
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

NOTICE = '系统历史线索不等于应用使用时长。开关机区间包含睡眠、待机和无人操作；缺失的结束时间不补算，线索不会加入日常工时统计。'
_lock = threading.Lock()
_cached = None
_cached_at = 0


def filetime_timestamp(value):
    if not isinstance(value, bytes) or len(value) < 8:
        return None
    stamp = (int.from_bytes(value[:8], 'little') - 116444736000000000) / 10000000
    return stamp if 946684800 <= stamp <= time.time() + 86400 else None


def summarize_history(payload, now=None):
    now = time.time() if now is None else now
    def valid(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 < value <= now
    events = sorted((row for row in payload.get('events', []) if isinstance(row, dict)
                     and row.get('id') in (12, 13) and valid(row.get('time'))), key=lambda row: row['time'])
    sessions, pending = [], None
    for row in events:
        if row['id'] == 12:
            if pending is not None:
                sessions.append({'start': pending, 'end': None, 'duration': None, 'status': 'shutdown_not_observed'})
            pending = row['time']
        elif pending is not None and row['time'] > pending:
            sessions.append({'start': pending, 'end': row['time'], 'duration': row['time'] - pending, 'status': 'closed'})
            pending = None
    if pending is not None:
        sessions.append({'start': pending, 'end': None, 'duration': None, 'status': 'shutdown_not_observed'})
    apps, seen = [], set()
    for row in payload.get('apps', []):
        if not isinstance(row, dict) or not isinstance(row.get('app'), str) or not valid(row.get('time')):
            continue
        name = ntpath.basename(row['app']).casefold()
        key = (name, row['time'])
        if name.endswith('.exe') and key not in seen:
            apps.append({'app': name, 'time': row['time'], 'kind': 'last_execution_clue'})
            seen.add(key)
    recent = [row for row in payload.get('recent', []) if isinstance(row, dict)
              and isinstance(row.get('name'), str) and valid(row.get('time'))]
    return {'scanned_at': now, 'lookback_days': 30, 'notice': NOTICE,
            'sessions': list(reversed(sessions)), 'closed_power_seconds': sum(row['duration'] or 0 for row in sessions),
            'apps': sorted(apps, key=lambda row: row['time'], reverse=True)[:300],
            'recent': sorted(recent, key=lambda row: row['time'], reverse=True)[:100],
            'errors': [str(error)[:300] for error in payload.get('errors', [])], 'evidence_only': True}


def _windows_clues():
    payload = {'events': [], 'apps': [], 'recent': [], 'errors': []}
    # Fixed script: never interpolates filenames, web inputs or registry values as commands.
    script = r"""
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ErrorActionPreference='Stop'
try {
 $items=@(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Kernel-General';Id=12,13;StartTime=(Get-Date).AddDays(-30)} -MaxEvents 2000)
 $out=@($items | ForEach-Object { @{id=$_.Id;time=([DateTimeOffset]$_.TimeCreated).ToUnixTimeMilliseconds()/1000.0} })
 ConvertTo-Json -InputObject $out -Compress
} catch { '[]'; exit 2 }
"""
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                capture_output=True, encoding='utf-8-sig', errors='replace', timeout=25,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        payload['events'] = json.loads(result.stdout.strip() or '[]')
        if result.returncode:
            payload['errors'].append('Windows开关机日志不可读或没有留存记录')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        payload['errors'].append('Windows开关机日志读取失败')
    try:
        import winreg
        # Windows keeps only latest execution observations in BAM; no duration is recorded here.
        import ctypes
        import ctypes.wintypes as wt
        token = wt.HANDLE()
        advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        advapi.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
        advapi.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
        advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.LPWSTR)]
        kernel.GetCurrentProcess.restype = wt.HANDLE
        kernel.CloseHandle.argtypes = [wt.HANDLE]
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise OSError('Token unavailable')
        try:
            length = wt.DWORD()
            advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
            buffer = ctypes.create_string_buffer(length.value)
            if not advapi.GetTokenInformation(token, 1, buffer, length, ctypes.byref(length)):
                raise OSError('SID unavailable')
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            sid_text = wt.LPWSTR()
            if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
                raise OSError('SID conversion unavailable')
            try:
                sid_value = sid_text.value
            finally:
                kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        finally:
            kernel.CloseHandle(token)
        path = r'SYSTEM\CurrentControlSet\Services\bam\State\UserSettings' + '\\' + sid_value
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            for i in range(winreg.QueryInfoKey(key)[1]):
                app, value, kind = winreg.EnumValue(key, i)
                stamp = filetime_timestamp(value) if kind == winreg.REG_BINARY else None
                if stamp and stamp >= time.time() - 30 * 86400:
                    payload['apps'].append({'app': app, 'time': stamp})
    except OSError:
        payload['errors'].append('应用执行线索不可读；不请求管理员权限')
    recent = Path(os.environ.get('APPDATA', '')) / 'Microsoft/Windows/Recent'
    try:
        for item in recent.glob('*.lnk'):
            stamp = item.stat().st_mtime
            if stamp >= time.time() - 30 * 86400:
                payload['recent'].append({'name': item.stem, 'time': stamp, 'kind': 'recent_shortcut_modified'})
    except OSError:
        payload['errors'].append('近期文件线索不可读')
    return payload


def scan_history(force=False):
    global _cached, _cached_at
    with _lock:
        if _cached is not None and not force and time.monotonic() - _cached_at < 300:
            return _cached
        payload = _windows_clues() if sys.platform == 'win32' else {'errors': ['系统历史线索仅支持Windows']}
        _cached = summarize_history(payload)
        _cached_at = time.monotonic()
        return _cached
