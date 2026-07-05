@echo off
title Smart Money Trader - First Time Setup (Online)
color 0A
cls
setlocal
set "ROOT=%~dp0"
set "RT=%ROOT%runtime"
set "PYVER=3.11.9"
set "PYZIP=python-%PYVER%-embed-amd64.zip"
set "PYURL=https://www.python.org/ftp/python/%PYVER%/%PYZIP%"
set "GETPIP=https://bootstrap.pypa.io/get-pip.py"

echo  ============================================
echo   SMART MONEY TRADER - First Time Setup
echo   (Online - downloads Python + packages)
echo  ============================================
echo.
echo  This needs an internet connection. It runs only once.
echo.

:: ---------------------------------------------------------------
:: 1) Download + unpack the Python runtime (if not already present)
:: ---------------------------------------------------------------
if exist "%RT%\python.exe" goto pip

echo  [1/3] Downloading Python %PYVER% runtime (about 11 MB)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest '%PYURL%' -OutFile '%TEMP%\smt_py.zip'"
if not exist "%TEMP%\smt_py.zip" (
    echo  [!] Download failed. Check your internet connection and run setup again.
    pause & exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Force '%TEMP%\smt_py.zip' '%RT%'"
del "%TEMP%\smt_py.zip" >nul 2>&1

:: Enable site-packages so pip-installed libraries can be imported
(
echo python311.zip
echo .
echo Lib\site-packages
echo import site
) > "%RT%\python311._pth"

:: ---------------------------------------------------------------
:: 2) Bootstrap pip into the runtime
:: ---------------------------------------------------------------
:pip
if exist "%RT%\Scripts\pip.exe" goto packages
echo  [2/3] Installing pip...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest '%GETPIP%' -OutFile '%TEMP%\get-pip.py'"
"%RT%\python.exe" "%TEMP%\get-pip.py" --no-warn-script-location
del "%TEMP%\get-pip.py" >nul 2>&1

:: ---------------------------------------------------------------
:: 3) Install the app's Python packages from PyPI
:: ---------------------------------------------------------------
:packages
if exist "%RT%\Lib\site-packages\fastapi" goto done
echo  [3/3] Installing packages from the internet (2-4 minutes)...
"%RT%\python.exe" -m pip install --no-warn-script-location -r "%ROOT%backend\requirements.txt"
if not exist "%RT%\Lib\site-packages\fastapi" (
    echo  [!] Package install failed. Run setup again or check your connection.
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
