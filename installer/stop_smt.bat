@echo off
title Stopping Smart Money Trader
echo Stopping Smart Money Trader...
:: Close the named engine window first (clean shutdown)
taskkill /fi "WindowTitle eq SMT Engine*" /f >nul 2>&1
:: Fallback: kill the bundled runtime python if it is still serving on 8000
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000 .*LISTENING"') do taskkill /f /pid %%p >nul 2>&1
echo.
echo Smart Money Trader stopped.
timeout /t 2 >nul
