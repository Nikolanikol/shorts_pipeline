@echo off
set PYTHON=C:\Users\krezi\AppData\Local\Programs\Python\Python311
set FFMPEG=C:\Users\krezi\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin
set PYTHONUTF8=1
set PATH=%PYTHON%;%PYTHON%\Scripts;%FFMPEG%;%PATH%
cd /d "%~dp0"
echo Starting Telegram bot...
"%PYTHON%\python.exe" -m bot.main
