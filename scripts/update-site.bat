@echo off
rem Envisoft WebX - saha guncelleme. Cift tikla calistir.
rem Ayni klasorde update-site.ps1 (ve onerilen fix-redis-persistence.ps1) olmali.
rem Ek parametreler aynen gecer, ornek: update-site.bat -SkipLogin

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Yonetici yetkisi isteniyor...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-site.ps1" %*
echo.
pause
