@echo off
title Logo Stamper - keep this window open
cd /d "%~dp0"
echo Starting Logo Stamper...
echo.
echo Keep this window open while you work.
echo Closing it will close the program.
echo.
runtime\LogoStamper.exe app\run.py
if errorlevel 1 (
  echo.
  echo Something went wrong - please show this window to your developer.
  pause
)
