@echo off
title Smart Money Trader
color 0A
cls
echo.
echo  ============================================
echo   SMART MONEY TRADER v1.0
echo  ============================================
echo.

set ROOT=%~dp0
set BACKEND=%ROOT%backend
set FRONTEND=%ROOT%frontend
set VENV=%BACKEND%\.venv

:: Check Python
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Python not found in PATH.
    pause
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
    echo [Setup] Done!
    echo.
)

:: Kill any already-running SMT windows so this acts as a restart too
taskkill /fi "WindowTitle eq SMT Backend*" /f >nul 2>&1
taskkill /fi "WindowTitle eq SMT Frontend*" /f >nul 2>&1
timeout /t 1 /nobreak >nul

echo Starting backend...
start "SMT Backend" cmd /k "cd /d %BACKEND% && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"

timeout /t 4 /nobreak >nul

echo Starting frontend...
start "SMT Frontend" cmd /k "cd /d %FRONTEND% && npm run dev"

timeout /t 3 /nobreak >nul

start http://localhost:5173

echo.
echo SMT is running!
echo Backend:  http://127.0.0.1:8000
echo Frontend: http://localhost:5173
echo.
pause
