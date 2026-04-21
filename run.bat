@echo off
set PYTHON=C:\Users\krezi\AppData\Local\Programs\Python\Python311
set FFMPEG=C:\Users\krezi\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin
set SP=%PYTHON%\Lib\site-packages\nvidia
set PYTHONUTF8=1
set PATH=%SP%\cublas\bin;%SP%\cudnn\bin;%SP%\cuda_runtime\bin;%SP%\cuda_nvrtc\bin;%PYTHON%;%PYTHON%\Scripts;%FFMPEG%;%PATH%
cd /d "%~dp0"
if "%~1"=="" (
    echo Shorts Pipeline
    echo.
    echo   run.bat process video.mp4    - process one video
    echo   run.bat process --inbox      - process all from inbox/
    echo   run.bat publish              - start TikTok scheduler
    echo   run.bat start                - inbox + publish
    echo   run.bat status               - queue status
    echo   run.bat logs                 - recent log entries
) else (
    "%PYTHON%\python.exe" controller.py %*
)
