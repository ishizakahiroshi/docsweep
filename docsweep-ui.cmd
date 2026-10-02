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
set "ROOT=D:\dev"

rem If a folder was dropped onto this .cmd, use it
if not "%~1"=="" set "ROOT=%~1"

rem docsweep repository (works even without pip install)
set "REPO=D:\dev\github\public\docsweep" & rem secrets-scan: allow

rem Port. The access token is generated randomly at each start (no fixed value).
rem To pin it, set the DOCSWEEP_TOKEN environment variable to a private value.
set "PORT=8765"

cd /d "%REPO%"
echo.
echo  docsweep Web UI
echo  (browser opens automatically / stop with Ctrl+C. The URL is shown below)
echo.
python -m docsweep serve --root "%ROOT%" --port %PORT%

echo.
echo  Stopped.
pause
