@echo off
chcp 65001 > nul
cd /d "%~dp0"
set "PORT=8787"
if not "%~1"=="" set "PORT=%~1"

echo.
echo   ============================================
echo    nodl - local no-watermark downloader
echo   ============================================
echo.
echo    port      : %PORT%
echo    url       : http://127.0.0.1:%PORT%
echo.
echo    closing this window stops the server.
echo.

where python > nul 2>&1
if errorlevel 1 goto nopython

start "" "http://127.0.0.1:%PORT%"
python -m nodl serve --port %PORT%
echo.
echo [exit code %ERRORLEVEL%] server stopped
pause
exit /b

:nopython
echo [ERROR] Python was not found in PATH.
echo.
echo   Install Python 3.8+ from https://www.python.org/downloads/
echo   IMPORTANT: tick "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1
