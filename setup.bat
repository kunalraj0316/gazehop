@echo off
cd /d "%~dp0"
py -3.11 -m venv .venv || (echo Python 3.11 needed & pause & exit /b 1)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo Done. Use run.bat
pause
