@echo off
title Smart Money Trader - First Time Setup (Offline)
color 0A
cls
setlocal
set "ROOT=%~dp0"
set "RT=%ROOT%runtime"
set "WHEELS=%ROOT%wheels"
set "PYZIP=%ROOT%runtime_src\python-embed.zip"

echo  ============================================
echo   SMART MONEY TRADER - First Time Setup
echo   (Offline - everything is bundled)
echo  ============================================
echo.
echo  No internet needed. This runs only once.
echo.

:: ---------------------------------------------------------------
:: 1) Unpack the bundled Python runtime (if not already present)
:: ---------------------------------------------------------------
if exist "%RT%\python.exe" goto packages

if not exist "%PYZIP%" (
    echo  [!] Bundled runtime not found at runtime_src\python-embed.zip
    echo      This installer may be incomplete. Please reinstall.
    pause & exit /b 1
)
echo  [1/2] Unpacking bundled Python runtime...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Force '%PYZIP%' '%RT%'"

:: Enable site-packages so installed libraries can be imported
(
echo python311.zip
echo .
echo Lib\site-packages
echo import site
) > "%RT%\python311._pth"

:: ---------------------------------------------------------------
:: 2) Install bundled packages fully offline (from wheels\)
:: ---------------------------------------------------------------
:packages
if exist "%RT%\Lib\site-packages\fastapi" goto done

echo  [2/2] Installing bundled packages (offline, 2-3 minutes)...
:: Locate the bundled pip wheel and run it directly (embeddable python has no pip yet)
set "PIPWHL="
for %%f in ("%WHEELS%\pip-*.whl") do set "PIPWHL=%%~ff"
if "%PIPWHL%"=="" (
    echo  [!] Bundled pip wheel not found in wheels\. Installer is incomplete.
    pause & exit /b 1
)
"%RT%\python.exe" "%PIPWHL%/pip" install --no-warn-script-location --no-index --find-links "%WHEELS%" -r "%ROOT%backend\requirements.txt" -t "%RT%\Lib\site-packages"
if not exist "%RT%\Lib\site-packages\fastapi" (
    echo  [!] Offline package install failed. Please reinstall the application.
    pause & exit /b 1
)

:done
echo.
echo  ============================================
echo   SETUP COMPLETE!
echo  ============================================
echo  Use "Start Smart Money Trader" to launch the app.
echo.
timeout /t 5 >nul
endlocal
