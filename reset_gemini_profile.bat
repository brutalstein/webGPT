@echo off
setlocal
cd /d "%~dp0"
echo Bu islem mevcut Gemini otomasyon profilini yedekleyip bos profil olusturur.
echo Google hesabina setup_gemini.bat ile yeniden girmen gerekir.
choice /C EH /N /M "Devam edilsin mi? [E=Evet, H=Hayir]: "
if errorlevel 2 exit /b 0
call start.bat --reset-profiles gemini
pause
