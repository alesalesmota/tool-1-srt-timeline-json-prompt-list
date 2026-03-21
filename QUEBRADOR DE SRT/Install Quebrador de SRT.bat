@echo off
setlocal
title Install Quebrador de SRT

echo.
echo Installing Quebrador de SRT...
echo.

set "BOOTSTRAP_PY=python"
where py >nul 2>nul
if not errorlevel 1 set "BOOTSTRAP_PY=py -3.10"

call %BOOTSTRAP_PY% -m venv "%~dp0.venv"
if errorlevel 1 (
  echo Could not create the virtual environment.
  pause
  exit /b 1
)

call "%~dp0.venv\Scripts\activate.bat"
if errorlevel 1 (
  echo Could not activate the virtual environment.
  pause
  exit /b 1
)

python -m pip install --upgrade pip
if errorlevel 1 (
  echo Could not update pip.
  pause
  exit /b 1
)

python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo App dependencies failed to install.
  pause
  exit /b 1
)

echo.
echo Install complete.
echo You can now start the app with:
echo   Run Quebrador de SRT.bat
echo.
pause
