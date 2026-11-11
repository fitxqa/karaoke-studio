@echo off
REM One-time setup to run the server on your own Windows PC.
REM Needs Python 3.11 (python.org) and ffmpeg (winget install Gyan.FFmpeg).
cd /d %~dp0
python -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
REM NVIDIA GPU? Replace the next line with:  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
if not exist .env copy .env.example .env
echo.
echo Setup done. Start the server with start_server.bat
pause
