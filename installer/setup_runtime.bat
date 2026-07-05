@echo off
title Smart Money Trader - First Time Setup
color 0A
set "ROOT=%~dp0"
set "RT=%ROOT%runtime"

echo  ============================================
echo   SMART MONEY TRADER - First Time Setup
echo  ============================================
echo.

if exist "%RT%\python.exe" goto packages

echo [1/2] Downloading Python runtime (11 MB, one time only)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip' -OutFile '%TEMP%\smt_pyembed.zip'"
if not exist "%TEMP%\smt_pyembed.zip" (
    echo  Download failed - check your internet connection and run setup again.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Force '%TEMP%\smt_pyembed.zip' '%RT%'"
del "%TEMP%\smt_pyembed.zip" >nul 2>&1

rem Enable site-packages in the embeddable runtime
(
echo python311.zip
echo .
echo Lib\site-packages
echo import site
) > "%RT%\python311._pth"

:packages
if exist "%RT%\Lib\site-packages\fastapi" goto done

echo [2/2] Installing packages from bundled archive (offline, 2-3 minutes)...
set "PIPWHL="
for %%f in ("%ROOT%wheels\pip-*.whl") do set "PIPWHL=%%~ff"
"%RT%\python.exe" "%PIPWHL%/pip" install --quiet --no-warn-script-location --no-index --find-links "%ROOT%wheels" -r "%ROOT%backend\requirements.txt" -t "%RT%\Lib\site-packages"
if not exist "%RT%\Lib\site-packages\fastapi" (
    echo  Package installation failed. Run this setup again or contact support.
    pause
    exit /b 1
)

:done
echo.
echo  Setup complete! Use "Start Smart Money Trader" to launch the app.
timeout /t 4 >nul
