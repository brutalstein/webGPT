@echo off
setlocal
cd /d "%~dp0"
set "OS_GEMINI_HEADED=0"
set "OS_GEMINI_MODE=cdp"
call start.bat --provider gemini
