@echo off
setlocal
title Quebrador de SRT

echo.
echo Starting Quebrador de SRT...
echo Keep this window open while the browser app is running.
echo.

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" "%~dp0run_srt_chunker.py" %*
exit /b %ERRORLEVEL%
