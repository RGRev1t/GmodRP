@echo off
chcp 65001 >nul
cd /d "%~dp0"
title GmodRP - kadrovyy audit

:loop
echo.
echo ============================================
echo   Zapusk bota... okno NE zakryvat!
echo   Log pi'shetsya v bot.log
echo ============================================
python bot.py
echo.
echo Bot ostanovilsya. Perepusk cherez 5 sekund.
echo (Zakroyte okno ili Ctrl+C, chtoby vyyti)
timeout /t 5 >nul
goto loop
