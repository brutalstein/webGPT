@echo off
setlocal
set "LOG_DIR=%LOCALAPPDATA%\OS\providers\gemini\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
start "" explorer.exe "%LOG_DIR%"
