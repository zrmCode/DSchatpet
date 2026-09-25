@echo off
rem 带控制台启动(能看到日志,调试用)
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
".venv\Scripts\python.exe" main.py %*
pause
