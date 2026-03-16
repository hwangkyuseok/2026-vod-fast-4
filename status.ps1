#Requires -Version 5.1

# UTF-8 output encoding
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding            = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$ErrorActionPreference = "Continue"

$Root    = $PSScriptRoot
$PidFile = Join-Path $Root ".pids.json"
$LogDir  = Join-Path $Root "storage\logs"

Write-Host ""
Write-Host "============================================="
Write-Host "  VOD Ad Overlay System  STATUS"
Write-Host "============================================="

if (-not (Test-Path $PidFile)) {
    Write-Host "  [stopped] No .pids.json - services are not running."
    Write-Host "============================================="
    Write-Host ""
    exit 0
}

$pidMap = Get-Content $PidFile -Raw | ConvertFrom-Json
$alive  = 0
$dead   = 0

# Log file map per service
$logMap = @{
    step1    = "step1.log"
    step2    = "step2.log"
    step3    = "step3.log"
    step4    = "step4.log"
    step5    = "step5_api.log"
    frontend = "frontend.log"
}

Write-Host ""
Write-Host ("  {0,-24} {1,-8} {2,-10} {3,-10} {4}" -f "Service", "PID", "Status", "CPU(s)", "Last log line")
Write-Host ("  " + ("-" * 90))

foreach ($entry in $pidMap.PSObject.Properties) {
    $svcName = $entry.Name
    # NOTE: $pid is a PowerShell reserved automatic variable.
    #       Using $procId to avoid the conflict.
    $procId  = [int]$entry.Value

    $proc = $null
    if ($procId -gt 0) {
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    }

    if ($proc) {
        $cpuSec = [math]::Round($proc.TotalProcessorTime.TotalSeconds, 1)
        $status = "[RUN]"
        $alive++
    } else {
        $cpuSec = "-"
        $status = "[--] "
        $dead++
    }

    # PowerShell 5.1 compatible null check (no ?? operator - that is PS7+ only)
    $lastLog = ""
    if ($logMap.ContainsKey($svcName)) {
        $logFile = Join-Path $LogDir $logMap[$svcName]
        if (Test-Path $logFile) {
            $rawLine = Get-Content $logFile -Tail 1 -Encoding UTF8 -ErrorAction SilentlyContinue
            if ($rawLine) { $lastLog = $rawLine } else { $lastLog = "" }
            if ($lastLog.Length -gt 19) { $lastLog = $lastLog.Substring(20) }
            if ($lastLog.Length -gt 70) { $lastLog = $lastLog.Substring(0, 67) + "..." }
        }
    }

    Write-Host ("  {0,-24} {1,-8} {2,-10} {3,-10} {4}" -f $svcName, $procId, $status, $cpuSec, $lastLog)
}

Write-Host ""
Write-Host ("  Running: $alive  |  Stopped: $dead")
Write-Host "---------------------------------------------"
Write-Host "  API : http://localhost:8000/docs"
Write-Host "  UI  : http://localhost:3000"
Write-Host "  Logs: $LogDir"
Write-Host "============================================="
Write-Host ""
