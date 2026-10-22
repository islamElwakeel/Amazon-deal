@echo off
chcp 65001 >nul
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python not found. Install it from python.org and tick "Add python.exe to PATH", then run this file again. & pause & exit /b 1)
echo Installing libraries...
python -m pip install --quiet requests beautifulsoup4 || (echo pip install failed & pause & exit /b 1)
python watcher.py --test || (pause & exit /b 1)
set /p TOKEN=Paste your Telegram bot token then press Enter: 
setx TG_BOT_TOKEN "%TOKEN%" >nul
setx TG_CHAT_ID 888676464 >nul
setx INTERVAL_MIN 5 >nul
for /f "delims=" %%p in ('python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%p"
echo start "" "%PYW%" "%~dp0watcher.py" > "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\amazon-deal-watch.bat"
set "TG_BOT_TOKEN=%TOKEN%"
set "TG_CHAT_ID=888676464"
set "INTERVAL_MIN=5"
start "" "%PYW%" "%~dp0watcher.py"
echo.
echo Done! You should get a Telegram message in a few seconds.
echo It will start by itself every time Windows starts.
pause
