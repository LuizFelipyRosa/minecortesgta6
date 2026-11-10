@echo off
cd /d "%~dp0"
python minecortes_gui.py
if errorlevel 1 pause
