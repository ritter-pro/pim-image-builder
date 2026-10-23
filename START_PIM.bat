@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto check
py -3 --version >nul 2>&1
if errorlevel 1 goto try_python
py -3 -m venv .venv
if errorlevel 1 goto fail
goto install
:try_python
python --version >nul 2>&1
if errorlevel 1 goto no_python
python -m venv .venv
if errorlevel 1 goto fail
:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
:check
".venv\Scripts\python.exe" -c "import fitz, PIL, pypdf, numpy" >nul 2>&1
if errorlevel 1 goto install
".venv\Scripts\python.exe" -c "import tkinter" >nul 2>&1
if errorlevel 1 goto no_tk
start "" ".venv\Scripts\pythonw.exe" "%~dp0pim_app.py"
exit /b 0
:no_tk
echo Python Tcl/Tk is missing. Modify the Python installation and enable Tcl/Tk and IDLE.
pause
exit /b 1
:no_python
echo Install Python 3.10 or newer with Python Launcher and Tcl/Tk.
echo If Python is already installed for the previous mockup tool, it can be reused.
pause
exit /b 1
:fail
echo Setup failed. Read the error above.
pause
exit /b 1
