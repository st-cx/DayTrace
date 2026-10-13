"""Current-user login startup. No administrator settings are changed."""
from pathlib import Path
import sys


def startup_enabled(root):
    if sys.platform != 'win32':
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            value, _ = winreg.QueryValueEx(key, 'DayTrace')
            target = Path(sys.executable) if getattr(sys, 'frozen', False) else Path(root) / 'manage_daytrace.py'
            return str(target).lower() in value.lower()
    except OSError:
        return False


def set_startup(root, enabled):
    if sys.platform != 'win32':
        if enabled:
            raise ValueError('登录启动仅支持Windows')
        return
    import winreg
    frozen = getattr(sys, 'frozen', False)
    manager = Path(sys.executable) if frozen else Path(root) / 'manage_daytrace.py'
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    if enabled and (not manager.is_file() or (not frozen and not pythonw.is_file())):
        raise ValueError('缺少后台启动文件，无法设置登录启动')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        try:
            existing, _ = winreg.QueryValueEx(key, 'DayTrace')
        except FileNotFoundError:
            existing = ''
        if existing and str(manager).lower() not in existing.lower():
            raise ValueError('已有其他DayTrace登录启动配置，请先确认其路径')
        if enabled:
            command = f'"{manager}" start --background' if frozen else f'"{pythonw}" "{manager}" start --background'
            winreg.SetValueEx(key, 'DayTrace', 0, winreg.REG_SZ, command)
        elif existing:
            winreg.DeleteValue(key, 'DayTrace')
