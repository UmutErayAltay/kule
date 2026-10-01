@echo off
REM Tek komut: kule'yi acar ve paneli browsera goturur.
REM Gecici dosya - kalici PATH degisikligi YAPILMADI.
REM Kalici yapmak istersen: bu dosyayi PATH'te olan bir klasore tasi
REM (ornegin %USERPROFILE%\bin) veya Masaustu'ne kisayol olarak koy.

if "%SCRIPTS%"=="" set "SCRIPTS=%APPDATA%\Python\Python313\Scripts"

if not exist "%SCRIPTS%\kule.exe" (
  echo [hata] kule kurulu degil.
  echo   gh repo clone UmutErayAltay/kule "%USERPROFILE%\Documents\kule"
  echo   cd /d "%USERPROFILE%\Documents\kule" ^&^& py -3 -m pip install -e .
  exit /b 1
)

start "" "%SCRIPTS%\kule.exe"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8790"
echo kule: http://127.0.0.1:8790
