@echo off
chcp 65001 > nul

set PYTHON=C:\Users\krezi\AppData\Local\Programs\Python\Python311
set FFMPEG=C:\Users\krezi\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin
set CUDA_DLLS=C:\Users\krezi\AppData\Local\Programs\Python\Python311\Lib\site-packages\nvidia\cublas\bin
set CUDNN_DLLS=C:\Users\krezi\AppData\Local\Programs\Python\Python311\Lib\site-packages\nvidia\cudnn\bin

set PYTHONUTF8=1
set PATH=%CUDA_DLLS%;%CUDNN_DLLS%;%PYTHON%;%PYTHON%\Scripts;%FFMPEG%;%PATH%

cd /d "%~dp0"

if "%~1"=="" (
    echo Использование:
    echo   run.bat "видео.mp4"
    echo   run.bat "видео.mp4" --platforms tiktok
) else (
    "%PYTHON%\python.exe" main.py %*
)
