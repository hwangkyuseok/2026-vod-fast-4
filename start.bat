@echo off
chcp 65001 > nul
echo.
echo  VOD Ad Overlay System - START
echo  PowerShell 스크립트를 실행합니다...
echo.
PowerShell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
pause
