@echo off
setlocal
cd /d "%~dp0.."
set "PYTHONPATH=%CD%\src"
if not defined SPECTROMAG_PYTHON set "SPECTROMAG_PYTHON=C:\ProgramData\anaconda3\envs\spectromag\python.exe"
"%SPECTROMAG_PYTHON%" -m spectromagpythontools.scripts.commissioning_gui --config "%CD%\config\instruments.local.toml"
if errorlevel 1 pause
endlocal
