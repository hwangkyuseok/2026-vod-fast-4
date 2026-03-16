#Requires -Version 5.1

# UTF-8 output encoding
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding            = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$ErrorActionPreference = "Continue"

$Root    = $PSScriptRoot
$PidFile = Join-Path $Root ".pids.json"

Write-Host ""
Write-Host "============================================="
Write-Host "  VOD Ad Overlay System  STOP"
Write-Host "============================================="

if (-not (Test-Path $PidFile)) {
    Write-Host "  [info] No .pids.json found - no services are running."
    Write-Host "============================================="
    Write-Host ""
    exit 0
}

$pidMap  = Get-Content $PidFile -Raw | ConvertFrom-Json
$stopped = 0
$missed  = 0

Write-Host ""

foreach ($entry in $pidMap.PSObject.Properties) {
    $svcName = $entry.Name
    # NOTE: $pid is a PowerShell reserved automatic variable.
    #       Using $procId to avoid the conflict.
    $procId  = [int]$entry.Value

    if ($procId -le 0) {
        Write-Host ("  [--]  " + $svcName.PadRight(24) + "PID 0  - start failed, skipping")
        continue
    }

    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue

    if ($proc) {
        # taskkill /F /T kills the entire process tree
        # (covers cmd.exe -> python, cmd.exe -> npm -> node, etc.)
        $result = & taskkill /F /T /PID $procId 2>&1
        $ok     = $LASTEXITCODE -eq 0
        if ($ok) {
            Write-Host ("  [XX]  " + $svcName.PadRight(24) + "PID $procId - stopped")
            $stopped++
        } else {
            Write-Host ("  [WW]  " + $svcName.PadRight(24) + "PID $procId - taskkill failed: $result")
        }
    } else {
        Write-Host ("  [--]  " + $svcName.PadRight(24) + "PID $procId - already stopped")
        $missed++
    }
}

Remove-Item -Path $PidFile -Force

Write-Host ""
Write-Host "---------------------------------------------"
Write-Host "  Stopped: $stopped  |  Already stopped: $missed"
Write-Host "  .pids.json removed"
Write-Host "============================================="
Write-Host ""
