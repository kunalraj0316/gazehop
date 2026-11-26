@echo off
cd /d "%~dp0"
echo Sit facing the camera. Test runs for about 6 seconds...
".venv\Scripts\python.exe" camtest.py 2>nul
echo.
echo Report saved to cam_report.txt
pause
