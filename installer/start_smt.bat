@echo off
title Smart Money Trader
color 0A
cls
echo.
echo  ============================================
echo   SMART MONEY TRADER
echo  ============================================
echo.

set "ROOT=%~dp0"
set "PY=%ROOT%runtime\python.exe"

:: --- First-run safety net: run setup if the runtime isn't ready ---
if not exist "%PY%" (
    echo  First-time setup required. Launching setup...
    if exist "%ROOT%setup_offline.bat" (
        call "%ROOT%setup_offline.bat"
    ) else (
        call "%ROOT%setup_online.bat"
    )
)
if not exist "%PY%" (
    echo  [!] Setup did not complete. Please run the "First Time Setup" shortcut.
    pause
    exit /b 1
)
if not exist "%ROOT%runtime\Lib\site-packages\fastapi" (
    echo  Packages missing - running setup again...
    if exist "%ROOT%setup_offline.bat" (
        call "%ROOT%setup_offline.bat"
    ) else (
        call "%ROOT%setup_online.bat"
    )
)

:: --- Restart cleanly: close any previous engine window ---
taskkill /fi "WindowTitle eq SMT Engine*" /f >nul 2>&1
timeout /t 1 /nobreak >nul

echo  Starting Smart Money Trader engine...
start "SMT Engine" /min cmd /k "cd /d "%ROOT%backend" && "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

:: Give the backend a few seconds to come up, then open the dashboard
timeout /t 6 /nobreak >nul
start http://127.0.0.1:8000/app/

echo.
echo  Smart Money Trader is running.
echo  Dashboard: http://127.0.0.1:8000/app/
echo.
echo  You can close this window. Use "Stop Smart Money Trader" to shut it down.
timeout /t 6 >nul
