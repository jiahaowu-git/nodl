@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo.
echo   ============================================
echo    nodl self check
echo   ============================================
echo.

where python > nul 2>&1
if errorlevel 1 goto nopython

python -c "import sys; print('python :', sys.version); print('exe    :', sys.executable)"
echo.
python selftest.py
echo.
echo --- server boot test ---
python -c "import nodl.server, nodl.cli, nodl.douyin, nodl.tiktok, nodl.browser; print('all modules import OK')"
echo.
pause
exit /b

:nopython
echo [ERROR] Python was not found in PATH.
echo   Install Python 3.8+ from https://www.python.org/downloads/
echo   IMPORTANT: tick "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1
