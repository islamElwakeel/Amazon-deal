@echo off
taskkill /f /im pythonw.exe >nul 2>&1
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\amazon-deal-watch.bat" >nul 2>&1
echo Stopped and removed from startup.
pause
