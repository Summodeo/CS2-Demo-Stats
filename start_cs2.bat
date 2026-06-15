@echo off
title CS2 - Insecure Mode
echo Starting CS2 with insecure mode...
echo.
echo Launch options: -insecure +exec server.cfg -disable_workshop_command_filtering
echo.

start "" "H:\Steam\steamapps\common\Counter-Strike Global Offensive\game\bin\win64\cs2.exe" -insecure +exec server.cfg -disable_workshop_command_filtering

echo CS2 is starting...
timeout /t 5 >nul
