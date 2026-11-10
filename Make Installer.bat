@echo off
setlocal EnableExtensions
title Tirso Compiler - making the installer
cd /d "%~dp0"

echo.
echo  Making the Tirso Compiler installer.
echo  This needs the internet the first time and takes about 3 to 10 minutes.
echo.

rem 1. Python environment (same one "Start Tirso Compiler.bat" sets up)
set "VENV=%~dp0.venv"
if not exist "%VENV%\Scripts\python.exe" (
  echo  First run "Start Tirso Compiler.bat" once, so Python is set up. Then run this file again.
  echo.
  pause
  exit /b 1
)
set "PY=%VENV%\Scripts\python.exe"

echo  [1/4] Getting the packaging tool, PyInstaller...
"%PY%" -m pip install --disable-pip-version-check -q -r "%~dp0requirements.txt" "pyinstaller>=6.10,<7"
if errorlevel 1 goto fail

echo  [2/4] Checking the program before packaging it...
"%PY%" -m tests.run_tests > "%TEMP%\tirso-selftest.txt" 2>&1
if errorlevel 1 (
  type "%TEMP%\tirso-selftest.txt"
  echo  The self-test failed, so no installer was made.
  goto fail
)

echo  [3/4] Building Tirso Compiler.exe...
if exist "%~dp0build" rmdir /s /q "%~dp0build"
"%PY%" -m PyInstaller --noconfirm --clean --windowed --log-level WARN ^
  --name "Tirso Compiler" ^
  --icon "%~dp0installer\tirso.ico" ^
  --paths "%~dp0." ^
  --add-data "%~dp0app\ui;app\ui" ^
  --collect-submodules app ^
  --collect-all webview ^
  --collect-all clr_loader ^
  --collect-all pythonnet ^
  --distpath "%~dp0build\dist" --workpath "%~dp0build\work" --specpath "%~dp0build" ^
  "%~dp0app\main.py"
if errorlevel 1 goto fail
set "OUT=%~dp0build\dist\Tirso Compiler"
if not exist "%OUT%\Tirso Compiler.exe" goto fail
rem templates and tools sit next to the .exe, where the program looks for them
robocopy "%~dp0templates" "%OUT%\templates" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto fail
robocopy "%~dp0tools" "%OUT%\tools" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto fail
copy /y "%~dp0README.txt" "%OUT%\README.txt" >nul
copy /y "%~dp0tools\LICENSES.txt" "%OUT%\LICENSES.txt" >nul

echo  [4/4] Making the installer...
set "ISCC="
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
  echo  Installing Inno Setup, the free installer maker, from Microsoft's app installer...
  winget install -e --id JRSoftware.InnoSetup --scope user --accept-package-agreements --accept-source-agreements
  if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
  if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
  if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
)
if not defined ISCC (
  echo.
  echo  Inno Setup could not be installed automatically.
  echo  The program itself is ready in: build\dist\Tirso Compiler
  echo  You can start "Tirso Compiler.exe" from there.
  goto fail
)
"%ISCC%" /Q "%~dp0installer\TirsoCompiler.iss"
if errorlevel 1 goto fail

echo.
echo  Done. The installer is in the "Installer" folder:
echo    Installer\Tirso Compiler Setup 1.0.0.exe
echo.
explorer "%~dp0Installer"
pause
exit /b 0

:fail
echo.
echo  Making the installer stopped because of the error above.
echo  Take a screenshot of this window and send it to Claude.
echo.
pause
exit /b 1
