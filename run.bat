@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -c "import webview, requests" 2>nul || python -m pip install -r requirements.txt
rem 加参数 debug 可打开开发者工具：run.bat debug
if /i "%1"=="debug" set SHIYING_DEBUG=1
python main.py
if errorlevel 1 pause
