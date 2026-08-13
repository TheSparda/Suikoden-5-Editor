@echo off
REM Double-click to launch the Suikoden V editor on Windows.
cd /d "%~dp0Editor"
where py >nul 2>nul && (py s5editor.py & goto :eof)
where python >nul 2>nul && (python s5editor.py & goto :eof)
echo Python 3 is not installed. Get it from https://www.python.org/downloads/
pause
