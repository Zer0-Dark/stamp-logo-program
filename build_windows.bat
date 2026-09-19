@echo off
REM Builds LogoStamper.exe. Run this once on a Windows machine.
REM Needs Python 3.10+ from python.org ("Add Python to PATH" ticked).

echo Installing build tools...
python -m pip install --upgrade pip
python -m pip install pillow pyinstaller
if errorlevel 1 goto fail

echo.
echo Building LogoStamper.exe ...
python -m PyInstaller ^
  --onefile ^
  --name LogoStamper ^
  --add-data "logostamper/static;logostamper/static" ^
  --hidden-import tkinter ^
  --hidden-import tkinter.filedialog ^
  --collect-submodules PIL ^
  --noconfirm ^
  run.py
if errorlevel 1 goto fail

echo.
echo ===========================================================
echo  Done. Give the user this single file:
echo     dist\LogoStamper.exe
echo ===========================================================
pause
exit /b 0

:fail
echo.
echo Build failed. Check the messages above.
pause
exit /b 1
