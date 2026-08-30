@echo off
setlocal EnableExtensions
title Smart Money Trader
color 0A
cls
echo.
echo  ============================================
echo   SMART MONEY TRADER v1.0
echo  ============================================
echo.

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend"
set "VENV=%BACKEND%\.venv"
set "LOGDIR=%ROOT%work\launcher-logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

:: Check Python
python.exe --version >nul 2>&1
if errorlevel 1 (
    echo [!] Python not found in PATH.
    if /i not "%TRADING_LAB_HIDDEN%"=="1" pause
    exit /b 1
)

:: Create venv if missing
if not exist "%VENV%\Scripts\python.exe" (
    echo [Setup] Creating virtual environment...
    python -m venv "%VENV%"
    echo [Setup] Installing packages - please wait...
    "%VENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip
    "%VENV%\Scripts\python.exe" -m pip install --quiet -r "%BACKEND%\requirements.txt"
    "%VENV%\Scripts\python.exe" -m pip install --quiet MetaTrader5
    if errorlevel 1 (
        echo [!] Dependency installation failed.
        if /i not "%TRADING_LAB_HIDDEN%"=="1" pause
        exit /b 1
    )
    echo [Setup] Done!
    echo.
)

where npm.cmd >nul 2>&1
if errorlevel 1 (
    echo [!] npm.cmd was not found in PATH.
    if /i not "%TRADING_LAB_HIDDEN%"=="1" pause
    exit /b 1
)

:: Reuse our own healthy listeners. Never kill an unrelated application merely
:: because it owns one of SMT's configured ports.
set "BACKEND_STATE=missing"
powershell.exe -NoLogo -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if(-not $c){exit 2}; $p=Get-CimInstance Win32_Process -Filter \"ProcessId=$($c.OwningProcess)\" -ErrorAction SilentlyContinue; $cmd=''; for($i=0;$p -and $i -lt 5;$i++){ $cmd += ' ' + $p.CommandLine; $p=Get-CimInstance Win32_Process -Filter \"ProcessId=$($p.ParentProcessId)\" -ErrorAction SilentlyContinue }; if($cmd -match 'smart-money-trader.+uvicorn app[.]main:app'){exit 0}; exit 1" >nul 2>&1
if not errorlevel 1 set "BACKEND_STATE=ready"
if errorlevel 1 if not errorlevel 2 set "BACKEND_STATE=conflict"

set "FRONTEND_STATE=missing"
powershell.exe -NoLogo -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if(-not $c){exit 2}; $cmd=(Get-CimInstance Win32_Process -Filter \"ProcessId=$($c.OwningProcess)\" -ErrorAction SilentlyContinue).CommandLine; if($cmd -match 'smart-money-trader.+vite'){exit 0}; exit 1" >nul 2>&1
if not errorlevel 1 set "FRONTEND_STATE=ready"
if errorlevel 1 if not errorlevel 2 set "FRONTEND_STATE=conflict"

if "%BACKEND_STATE%"=="conflict" (
    echo [!] Port 8000 is occupied by another application. Nothing was stopped.
    exit /b 1
)
if "%FRONTEND_STATE%"=="conflict" (
    echo [!] Port 5173 is occupied by another application. Nothing was stopped.
    exit /b 1
)

if /i "%~1"=="--check" (
    echo Smart Money Trader launcher check passed.
    echo Backend port state: %BACKEND_STATE%
    echo Frontend port state: %FRONTEND_STATE%
    echo Hidden-launch mode: %TRADING_LAB_HIDDEN%
    exit /b 0
)

if "%BACKEND_STATE%"=="ready" (
    echo Backend is already running.
) else (
    echo Starting backend...
    if /i "%TRADING_LAB_HIDDEN%"=="1" (
        start "" /b "%VENV%\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir "%BACKEND%" >>"%LOGDIR%\backend.log" 2>&1
    ) else (
        start "SMT Backend" cmd.exe /k "cd /d ""%BACKEND%"" && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
    )
)

ping 127.0.0.1 -n 5 >nul

if "%FRONTEND_STATE%"=="ready" (
    echo Frontend is already running.
) else (
    echo Starting frontend...
    if /i "%TRADING_LAB_HIDDEN%"=="1" (
        start "" /b cmd.exe /d /c "cd /d ""%FRONTEND%"" && npm.cmd run dev -- --host 127.0.0.1 --port 5173 >>""%LOGDIR%\frontend.log"" 2>&1"
    ) else (
        start "SMT Frontend" cmd.exe /k "cd /d ""%FRONTEND%"" && npm.cmd run dev"
    )
)

set /a "FRONTEND_READY_TRIES=0"
:wait_for_frontend
curl.exe --fail --silent --show-error --max-time 2 "http://127.0.0.1:5173/" >nul 2>&1
if not errorlevel 1 goto frontend_ready
set /a "FRONTEND_READY_TRIES+=1"
if %FRONTEND_READY_TRIES% GEQ 60 (
    echo [!] Frontend did not become ready within 60 seconds.
    exit /b 1
)
ping 127.0.0.1 -n 2 >nul
goto wait_for_frontend

:frontend_ready

start "" "http://127.0.0.1:5173/"

echo.
echo SMT is running!
echo Backend:  http://127.0.0.1:8000
echo Frontend: http://localhost:5173
echo.
if /i not "%TRADING_LAB_HIDDEN%"=="1" pause
endlocal & exit /b 0
