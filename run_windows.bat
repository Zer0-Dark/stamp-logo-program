@echo off
REM Run from source without building an exe (needs Python + Pillow).
python -m pip install pillow >nul 2>&1
python run.py
pause
