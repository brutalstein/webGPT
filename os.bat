@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title OS - Kisisel AI Terminali
set "PYTHONUTF8=1"
set "OS_GEMINI_HEADED=0"
set "OS_GEMINI_MODE=cdp"

set "PYTHON_BOOTSTRAP="
where py >nul 2>&1
if %errorlevel%==0 set "PYTHON_BOOTSTRAP=py -3"

if not defined PYTHON_BOOTSTRAP (
    where python >nul 2>&1
    if %errorlevel%==0 set "PYTHON_BOOTSTRAP=python"
)

if not defined PYTHON_BOOTSTRAP (
    echo [KRITIK HATA] Python bulunamadi.
    echo Python 3.10 veya daha yeni bir surum kur ve Add Python to PATH secenegini etkinlestir.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [KURULUM] OS sanal ortami olusturuluyor...
    %PYTHON_BOOTSTRAP% -m venv .venv
    if errorlevel 1 (
        echo [KRITIK HATA] Sanal ortam olusturulamadi.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" bootstrap.py %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo OS hata koduyla kapandi: %EXIT_CODE%
    pause
)
exit /b %EXIT_CODE%
