@echo off
chcp 65001 > nul
echo.
echo  VOD Ad Overlay System - STOP
echo  PowerShell 스크립트를 실행합니다...
echo.
PowerShell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
pause
