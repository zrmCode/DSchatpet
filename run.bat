@echo off
rem 无控制台窗口启动桌宠
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
start "" ".venv\Scripts\pythonw.exe" main.py %*
