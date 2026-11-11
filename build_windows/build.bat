@echo off
setlocal EnableExtensions
title Building KaraokeStudio-Setup.exe
REM Builds dist\KaraokeStudio-Setup.exe: one installer with Python, the AI tools, ffmpeg and the app.
REM Needs internet (about 1.5 GB of downloads) and 20-40 minutes. Run it from anywhere.

set "HERE=%~dp0"
for %%I in ("%HERE%..") do set "ROOT=%%~fI"
REM Build in a short folder: some AI packages have very deep file paths and Windows limits paths to 260 characters.
set "WORK=%USERPROFILE%\ksb"
set "OUT=%WORK%\KaraokeStudio"
set "CACHE=%WORK%\cache"
set "DIST=%ROOT%\dist"
set "PY=%OUT%\python\python.exe"

echo.
echo  ===== Building Karaoke Studio installer =====
echo  This takes 20-40 minutes. You can leave it running.
echo.

if exist "%OUT%" rmdir /s /q "%OUT%"
if not exist "%CACHE%" mkdir "%CACHE%"
if not exist "%DIST%" mkdir "%DIST%"
mkdir "%OUT%"

echo [1/7] Python 3.11 (portable) ...
if not exist "%CACHE%\python-embed.zip" curl.exe -L --fail -o "%CACHE%\python-embed.zip" https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip || goto :fail
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Force -LiteralPath '%CACHE%\python-embed.zip' -DestinationPath '%OUT%\python'" || goto :fail
REM Search paths for the portable Python: its own files, installed packages, and the app folder.
> "%OUT%\python\python311._pth" (
  echo python311.zip
  echo .
  echo Lib\site-packages
  echo ..\server
  echo import site
)

echo [2/7] pip ...
if not exist "%CACHE%\get-pip.py" curl.exe -L --fail -o "%CACHE%\get-pip.py" https://bootstrap.pypa.io/get-pip.py || goto :fail
"%PY%" "%CACHE%\get-pip.py" --no-warn-script-location || goto :fail

echo [3/7] AI tools - vocal remover, lyrics, YouTube downloader (big download) ...
"%PY%" -m pip install --no-warn-script-location --retries 10 --timeout 60 -r "%ROOT%\server\requirements.txt" || goto :fail
"%PY%" -c "import torch, demucs, faster_whisper, librosa, yt_dlp, fastapi, uvicorn; print('  AI tools OK, torch', torch.__version__)" || goto :fail
if exist "%OUT%\python\Scripts" rmdir /s /q "%OUT%\python\Scripts"

echo [4/7] ffmpeg ...
if not exist "%CACHE%\ffmpeg.zip" curl.exe -L --fail -o "%CACHE%\ffmpeg.zip" https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip || goto :fail
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t = Join-Path '%CACHE%' 'ff'; if (Test-Path $t) { Remove-Item -Recurse -Force $t }; Expand-Archive -Force '%CACHE%\ffmpeg.zip' $t; $b = (Get-ChildItem $t -Recurse -Filter ffmpeg.exe | Select-Object -First 1).Directory.FullName; $d = Join-Path '%OUT%' 'ffmpeg\bin'; New-Item -ItemType Directory -Force $d | Out-Null; Copy-Item (Join-Path $b 'ffmpeg.exe'), (Join-Path $b 'ffprobe.exe') -Destination $d" || goto :fail
if not exist "%OUT%\ffmpeg\bin\ffmpeg.exe" goto :fail

echo [5/7] App files ...
robocopy "%ROOT%\server\app" "%OUT%\server\app" /E /XD __pycache__ /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :fail

echo [6/7] KaraokeStudio.exe ...
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
"%CSC%" /nologo /target:exe /platform:x64 /optimize /win32icon:"%HERE%icon.ico" /out:"%OUT%\KaraokeStudio.exe" "%HERE%launcher.cs" || goto :fail

echo [7/7] Packing everything into one Setup.exe (5-15 minutes) ...
set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" goto :haveiscc
echo   Downloading Inno Setup (free installer maker) ...
curl.exe -L --fail -o "%CACHE%\innosetup.exe" https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe || curl.exe -L --fail -o "%CACHE%\innosetup.exe" https://files.jrsoftware.org/is/6/innosetup-6.7.3.exe || goto :fail
for %%F in ("%CACHE%\innosetup.exe") do if %%~zF LSS 1000000 ( echo   Inno Setup download was not a program. & goto :fail )
"%CACHE%\innosetup.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /DIR="%LOCALAPPDATA%\Programs\Inno Setup 6"
set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" ( echo   Inno Setup could not be installed. & goto :fail )
:haveiscc
"%ISCC%" /Q "/DSrcDir=%OUT%" "/DOutDir=%DIST%" "%HERE%KaraokeStudio.iss" || goto :fail

echo.
echo  ===== Done! =====
echo  Installer: %DIST%\KaraokeStudio-Setup.exe
echo  Portable folder (no install needed): %OUT%
echo.
if not defined CI explorer /select,"%DIST%\KaraokeStudio-Setup.exe"
if not defined CI pause
exit /b 0

:fail
echo.
echo  ===== Build failed - see the messages above. =====
echo  Check your internet connection and run build.bat again (finished downloads are reused).
if not defined CI pause
exit /b 1
