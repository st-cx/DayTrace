@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo 构建源代码需要 Python 3.10 或更新版本。已经打包的 DayTrace.exe 无需安装 Python。
  pause
  exit /b 1
)
if not exist ".build-venv\Scripts\python.exe" py -3 -m venv .build-venv
if errorlevel 1 exit /b 1
".build-venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-build.txt
if errorlevel 1 exit /b 1
".build-venv\Scripts\python.exe" -m PyInstaller --noconfirm --onedir --windowed --name DayTrace --icon assets\DayTrace.ico --version-file assets\version-info.txt --add-data "web;web" --add-data "assets/DayTrace.ico;assets" manage_daytrace.py
if errorlevel 1 (
  echo 构建失败，请查看以上错误。
) else (
  echo 已生成 dist\DayTrace\DayTrace.exe。请保留同目录的 _internal 文件夹。
)
pause
