@echo off
chcp 65001 > nul

:: Пути
set PYTHON=C:\Users\krezi\AppData\Local\Programs\Python\Python311
set FFMPEG=C:\Users\krezi\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin

:: Переменные окружения
set PYTHONUTF8=1
set PATH=%PYTHON%;%PYTHON%\Scripts;%FFMPEG%;%PATH%

:: Переходим в папку скрипта
cd /d "%~dp0"

:: Запуск
if "%~1"=="" (
    echo Использование:
    echo   run.bat "видео.mp4"
    echo   run.bat "видео.mp4" --platforms tiktok
    echo   run.bat "видео.mp4" --platforms tiktok youtube_shorts
) else (
    python main.py %*
)
