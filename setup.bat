@echo off
title Smart Money Trader — Setup
color 0A
cls

echo.
echo  ============================================
echo   SMART MONEY TRADER — Setup Wizard
echo   ICT / SMC Algorithmic Trading Platform
echo  ============================================
echo.

set "INSTALL_DIR=%~dp0"
set "BACKEND=%INSTALL_DIR%backend"
set "FRONTEND=%INSTALL_DIR%frontend"

echo [1/4] Checking Python...
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo  Python not found. Opening download page...
    start https://www.python.org/downloads/
    echo  Install Python 3.11+, check "Add to PATH", then run setup again.
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version') do echo  Found: %%i

echo.
echo [2/4] Checking Node.js...
node --version >nul 2>&1
if %errorLevel% neq 0 (
    echo  Node.js not found. Opening download page...
    start https://nodejs.org/
    echo  Install Node.js LTS, then run setup again.
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('node --version') do echo  Found: Node %%i

echo.
echo [3/4] Setting up Python environment...
cd /d "%BACKEND%"
if not exist ".venv" (
    echo  Creating virtual environment...
    python -m venv .venv
)
echo  Installing packages...
.venv\Scripts\python -m pip install --quiet --upgrade pip
.venv\Scripts\python -m pip install --quiet -r requirements.txt
.venv\Scripts\python -m pip install --quiet MetaTrader5
echo  Done!

echo.
echo [4/4] Setting up Frontend...
cd /d "%FRONTEND%"
echo  Installing Node packages (this takes 2-3 minutes)...
call npm install --silent
echo  Done!

echo.
echo  Creating config files...
cd /d "%BACKEND%"
if not exist "mt4_config.json" (
    echo {> mt4_config.json
    echo   "login": null,>> mt4_config.json
    echo   "password": null,>> mt4_config.json
    echo   "server": "VantageMarkets-Demo",>> mt4_config.json
    echo   "currency": "USD",>> mt4_config.json
    echo   "mode": "paper",>> mt4_config.json
    echo   "lot_sizes": {"BTCUSD": 0.01, "ETHUSD": 0.01, "XAUUSD+": 0.01},>> mt4_config.json
    echo   "live_login": null,>> mt4_config.json
    echo   "live_password": null,>> mt4_config.json
    echo   "live_server": null>> mt4_config.json
    echo }>> mt4_config.json
    echo  Created mt4_config.json
) else (
    echo  mt4_config.json already exists — keeping your settings
)

echo.
echo  ============================================
echo   SETUP COMPLETE!
echo  ============================================
echo.
echo  Next steps:
echo    1. Edit backend\mt4_config.json with your MT5 credentials
echo    2. Double-click start_smt.bat to launch
echo    3. Open http://localhost:5173 in your browser
echo.
echo  Press any key to launch SMT now...
pause >nul
call "%INSTALL_DIR%start_smt.bat"
