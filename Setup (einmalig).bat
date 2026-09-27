@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ===============================================
echo    Eden.tools Downloader  -  Einrichtung
echo ===============================================
echo.

set "VER="
py -3.13 -c "import sys" >nul 2>&1 && set "VER=3.13"
if not defined VER ( py -3.12 -c "import sys" >nul 2>&1 && set "VER=3.12" )
if not defined VER ( py -3.11 -c "import sys" >nul 2>&1 && set "VER=3.11" )
if not defined VER goto nopy

echo Verwende Python %VER%.
echo Installiere Komponenten (pywebview, yt-dlp, ffmpeg) ...
py -%VER% -m pip install --upgrade pip
py -%VER% -m pip install --upgrade pywebview yt-dlp imageio-ffmpeg
if errorlevel 1 goto fehler

echo.
echo Fertig! Du kannst jetzt "Eden Downloader starten.bat" benutzen.
echo.
pause
exit /b 0

:fehler
echo.
echo ====== FEHLER bei der Installation ======
echo Bitte den Text oben pruefen (oft fehlt nur eine Internetverbindung).
echo.
pause
exit /b 1

:nopy
echo ====== Python 3.13 nicht gefunden ======
echo Bitte Python 3.11, 3.12 ODER 3.13 installieren (NICHT 3.14):
echo     winget install Python.Python.3.13
echo oder hier: https://www.python.org/downloads/
echo (Bei der Installation "Add python.exe to PATH" und "py launcher" aktiviert lassen.)
echo.
pause
exit /b 1
