@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "VER="
py -3.13 -c "import sys" >nul 2>&1 && set "VER=3.13"
if not defined VER ( py -3.12 -c "import sys" >nul 2>&1 && set "VER=3.12" )
if not defined VER ( py -3.11 -c "import sys" >nul 2>&1 && set "VER=3.11" )
if not defined VER goto nopy

REM Ohne Konsolenfenster starten (pyw)
start "" pyw -%VER% "%~dp0eden_downloader.py"
exit /b 0

:nopy
echo Python 3.11/3.12/3.13 nicht gefunden. Bitte zuerst "Setup (einmalig).bat" ausfuehren.
pause
exit /b 1
