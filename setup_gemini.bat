@echo off
setlocal
cd /d "%~dp0"
call start.bat --setup gemini
if errorlevel 1 pause
