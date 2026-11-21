@echo off
cd /d "%~dp0"
start "" pyw -3.12 app.py || start "" pythonw app.py
