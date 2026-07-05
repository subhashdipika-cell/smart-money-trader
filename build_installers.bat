@echo off
:: ============================================================
::  Smart Money Trader - one-click installer builder
::  Run this on a WINDOWS PC from the project root.
::  Produces both setup .exe files in installer_output\
::
::  Build-machine prerequisites (NOT needed on the user's PC):
::    - NSIS          https://nsis.sourceforge.io (auto-detected below)
::    - Node.js       (to build the dashboard)
::    - Python 3.x    (to download offline wheels)
::    - Internet      (to fetch the runtime + wheels for the offline build)
:: ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"
color 0B

set "PYVER=3.11.9"
set "PYZIP=python-%PYVER%-embed-amd64.zip"
set "PYURL=https://www.python.org/ftp/python/%PYVER%/%PYZIP%"
set "ASSETS=build_assets"
set "WHEELS=wheels"

echo  ============================================
echo   SMART MONEY TRADER - Installer Builder
echo  ============================================
echo.

:: --- 0) Find NSIS (makensis) -------------------------------
set "MAKENSIS="
where makensis >nul 2>&1 && set "MAKENSIS=makensis"
if not defined MAKENSIS if exist "%ProgramFiles(x86)%\NSIS\makensis.exe" set "MAKENSIS=%ProgramFiles(x86)%\NSIS\makensis.exe"
if not defined MAKENSIS if exist "%ProgramFiles%\NSIS\makensis.exe" set "MAKENSIS=%ProgramFiles%\NSIS\makensis.exe"
if not defined MAKENSIS if exist "%LOCALAPPDATA%\Programs\NSIS\makensis.exe" set "MAKENSIS=%LOCALAPPDATA%\Programs\NSIS\makensis.exe"
if defined MAKENSIS goto nsis_ok
echo  [!] NSIS not found. Install it from https://nsis.sourceforge.io
pause
exit /b 1
:nsis_ok
echo  Using NSIS: %MAKENSIS%
if not exist installer_output mkdir installer_output

:: --- 1) Build the dashboard (always, to guarantee the /app base) ---
echo.
echo  [1/5] Building the dashboard (frontend)...
where npm >nul 2>&1
if errorlevel 1 (
    echo  npm not found - checking for a usable prebuilt dist...
    findstr /C:"/app/assets" frontend\dist\index.html >nul 2>&1
    if errorlevel 1 (
        echo  [!] frontend\dist is missing or built with the wrong base path,
        echo      and Node.js/npm is not installed. Install Node.js and re-run.
        pause & exit /b 1
    )
    echo  Existing frontend\dist looks correct - using it.
) else (
    pushd frontend
    call npm install
    call npm run build
    popd
)
if not exist "frontend\dist\index.html" ( echo  [!] Dashboard build failed. & pause & exit /b 1 )

:: --- 2) Build the ONLINE installer (no downloads needed) ----
echo.
echo  [2/5] Building ONLINE (small) installer...
pushd installer
"%MAKENSIS%" smt_online.nsi
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" ( echo  [!] Online build failed. & pause & exit /b 1 )

:: --- 3) Download the bundled Python runtime -----------------
echo.
echo  [3/5] Preparing offline runtime...
if not exist "%ASSETS%" mkdir "%ASSETS%"
if not exist "%ASSETS%\%PYZIP%" (
    echo        Downloading %PYZIP% ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest '%PYURL%' -OutFile '%ASSETS%\%PYZIP%'"
)
if not exist "%ASSETS%\%PYZIP%" ( echo  [!] Could not download Python runtime. & pause & exit /b 1 )

:: --- 4) Download offline pip wheels (Windows / Python 3.11) --
echo.
echo  [4/5] Downloading offline packages (wheels)...
where python >nul 2>&1
if errorlevel 1 ( echo  [!] Python not found - needed to fetch wheels. & pause & exit /b 1 )
if not exist "%WHEELS%" mkdir "%WHEELS%"
python -m pip download -r backend\requirements.txt -d "%WHEELS%" ^
    --only-binary=:all: --platform win_amd64 --python-version 311
if errorlevel 1 (
    echo  [!] Some packages had no Windows/py3.11 wheel.
    echo      Check backend\requirements.txt versions, then re-run.
    pause & exit /b 1
)
:: pip itself (for the offline bootstrap) + build helpers
python -m pip download pip setuptools wheel -d "%WHEELS%" ^
    --only-binary=:all: --platform win_amd64 --python-version 311

:: --- 5) Build the OFFLINE self-contained installer ----------
echo.
echo  [5/5] Building OFFLINE (self-contained) installer...
pushd installer
"%MAKENSIS%" smt_offline.nsi
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" ( echo  [!] Offline build failed. & pause & exit /b 1 )

echo.
echo  ============================================
echo   DONE!  Files are in installer_output\
echo     - SmartMoneyTrader_Setup_Online.exe
echo     - SmartMoneyTrader_Setup_Offline_SelfContained.exe
echo  ============================================
echo.
pause
endlocal
