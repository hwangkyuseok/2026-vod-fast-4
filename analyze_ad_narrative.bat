@echo off
chcp 65001 >nul
cd /d "%~dp0backend"

echo.
echo ==============================================
echo   Ad Narrative Analysis (Qwen2-VL v2.5)
echo ==============================================
echo   Analyzes all ads and updates target_narrative
echo   with a 4-dimension English sentence:
echo     1. Category   2. Audience
echo     3. Core Message   4. Ad Vibe
echo.
echo   Already-analyzed ads are automatically skipped.
echo   Press Ctrl+C to stop and resume later.
echo   Logs: storage\logs\analyze_ad_narrative.log
echo ==============================================
echo.

if "%1"=="--dry-run" (
    "%~dp0.venv\Scripts\python.exe" analyze_ad_narrative.py --dry-run
) else if "%1"=="--limit" (
    "%~dp0.venv\Scripts\python.exe" analyze_ad_narrative.py --limit %2
) else (
    "%~dp0.venv\Scripts\python.exe" analyze_ad_narrative.py %*
)

echo.
echo Done.
pause
