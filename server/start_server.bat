@echo off
title Karaoke Studio (keep this window open)
cd /d %~dp0
set "PATH=%~dp0..\ffmpeg\bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
call .venv\Scripts\activate
python -m pip install -U yt-dlp >nul 2>nul
echo.
echo  Karaoke Studio is running at http://localhost:8000
echo  Keep this window open while you sing. Close it to stop.
echo.
start "" http://localhost:8000
uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
