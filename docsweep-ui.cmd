@echo off
setlocal
rem ============================================================
rem  docsweep Web UI launcher (double-click to start)
rem  - The browser opens automatically. Stop with Ctrl+C.
rem  - Drag a folder onto this .cmd to scan that folder.
rem  NOTE: keep this file ASCII-only. cmd.exe parses .cmd with
rem        the OEM codepage, so Japanese text here breaks it.
rem ============================================================

rem Default scan root (edit to your own dev folder)
set "ROOT=C:\dev"

rem If a folder was dropped onto this .cmd, use it
if not "%~1"=="" set "ROOT=%~1"

rem docsweep repository (works even without pip install)
set "REPO=C:\dev\github\public\docsweep"

rem Port and fixed access token (URL stays the same every time)
set "PORT=8765"
set "TOKEN=docsweep"

cd /d "%REPO%"
echo.
echo  docsweep Web UI
echo  URL: http://127.0.0.1:%PORT%/?token=%TOKEN%
echo  (browser opens automatically / stop with Ctrl+C)
echo.
python -m docsweep serve --root "%ROOT%" --port %PORT% --token %TOKEN%

echo.
echo  Stopped.
pause
