@echo off
setlocal
rem ============================================================
rem  docsweep Web UI launcher (double-click to start)
rem  - The browser opens automatically. Stop with Ctrl+C.
rem  - Drag a folder onto this .cmd to scan that folder.
rem  NOTE: keep this file ASCII-only. cmd.exe parses .cmd with
rem        the OEM codepage, so Japanese text here breaks it.
rem ============================================================

rem Scan root: the "roots" in ~/.docsweep/config.yaml. A folder dropped onto
rem this .cmd wins. With neither, docsweep scans the folder it starts in.
rem No machine-specific path is written here.
set "ROOT_ARG="
if not "%~1"=="" set ROOT_ARG=--root "%~1"

rem docsweep repository: the folder this .cmd is in (works even without pip install).
rem If you copy this .cmd elsewhere, set the DOCSWEEP_REPO environment variable.
set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"
if defined DOCSWEEP_REPO set "REPO=%DOCSWEEP_REPO%"

rem Port. The access token is generated randomly at each start (no fixed value).
rem To pin it, set the DOCSWEEP_TOKEN environment variable to a private value.
set "PORT=8765"

cd /d "%REPO%"
echo.
echo  docsweep Web UI
echo  (browser opens automatically / stop with Ctrl+C. The URL is shown below)
echo.
python -m docsweep serve %ROOT_ARG% --port %PORT%

echo.
echo  Stopped.
pause
