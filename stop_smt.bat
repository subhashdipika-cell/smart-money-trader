@echo off
title Stopping SMT
echo Stopping Smart Money Trader...
taskkill /f /im python.exe /t 2>nul
taskkill /f /im node.exe /t 2>nul
echo.
echo Smart Money Trader stopped.
timeout /t 2 /nobreak >nul
