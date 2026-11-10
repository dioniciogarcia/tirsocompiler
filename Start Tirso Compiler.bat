@echo off
setlocal EnableExtensions
title Tirso Compiler
cd /d "%~dp0"

set "VENV=%~dp0.venv"
set "MARKER=%VENV%\tirso-setup-2.txt"
if exist "%MARKER%" goto run

echo.
echo  Setting up Tirso Compiler (first time, or after an update).
echo  This needs the internet and takes about 1 to 3 minutes.
echo.

set "PYEXE="
if exist "%VENV%\Scripts\python.exe" set "PYEXE=%VENV%\Scripts\python.exe"
for %%V in (3.12 3.13) do (
  if not defined PYEXE (
    for /f "delims=" %%P in ('py -%%V -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%P"
  )
)
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

if not defined PYEXE (
  echo  Python 3.12 is not installed. Installing it now from Microsoft's app installer...
  echo.
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)

if not defined PYEXE (
  echo.
  echo  Python could not be installed automatically.
  echo  Please install "Python 3.12" from the Microsoft Store, then run this file again.
  echo.
  pause
  exit /b 1
)

echo  Using Python: %PYEXE%
if not exist "%VENV%\Scripts\python.exe" "%PYEXE%" -m venv "%VENV%"
if errorlevel 1 goto fail
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -r "%~dp0requirements.txt"
if errorlevel 1 goto fail
echo done> "%MARKER%"
echo.
echo  Setup finished.

:run
start "" "%VENV%\Scripts\pythonw.exe" "%~dp0app\main.py"
exit /b 0

:fail
echo.
echo  Setup stopped because of the error above.
echo  Take a screenshot of this window and send it to Claude.
echo.
pause
exit /b 1
