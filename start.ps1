#Requires -Version 5.1
param(
    [switch]$SkipFrontend
)

# UTF-8 output encoding
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding            = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$ErrorActionPreference = "Stop"

# Paths
$Root      = $PSScriptRoot
$Backend   = Join-Path $Root "backend"
$Frontend  = Join-Path $Root "frontend"
$LogDir    = Join-Path $Root "storage\logs"
$PidFile   = Join-Path $Root ".pids.json"
$PythonExe = Join-Path $Root ".venv\Scripts\python.exe"

# Validate
if (-not (Test-Path $PythonExe)) {
    Write-Host "[ERROR] Virtual environment not found: $PythonExe"
    Write-Host "        Please create a venv and install packages first."
    exit 1
}
if (-not (Test-Path $Backend)) {
    Write-Host "[ERROR] backend directory not found: $Backend"
    exit 1
}

# Prevent duplicate start
if (Test-Path $PidFile) {
    $existing = Get-Content $PidFile -Raw | ConvertFrom-Json
    $aliveNames = @(
        $existing.PSObject.Properties | Where-Object {
            Get-Process -Id ([int]$_.Value) -ErrorAction SilentlyContinue
        } | Select-Object -ExpandProperty Name
    )
    if ($aliveNames.Count -gt 0) {
        Write-Host "[WARN] Services already running: $($aliveNames -join ', ')"
        Write-Host "       Run .\stop.ps1 first."
        exit 1
    }
    Write-Host "[INFO] Stale .pids.json found (dead processes) - cleaning up and restarting."
    Remove-Item $PidFile
}

# Create log directory
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# PID map
$pidMap = @{}

# Start-Svc
# Uses cmd.exe /c to wrap execution, enabling:
#   - WindowStyle=Hidden (no console window popup)
#   - Output redirection to log file
# NOTE: ArgumentList is passed as a single string to avoid
#       PowerShell 5.1 array-null issues with Start-Process.
function Start-Svc {
    param(
        [string] $Name,
        [string] $Exe,
        [string] $CmdArgs,
        [string] $WorkDir,
        [string] $LogFile = ""
    )

    try {
        if ($LogFile) {
            # cmd.exe /c wrapper: allows WindowStyle=Hidden AND log redirection.
            #
            # IMPORTANT: cmd.exe /c argument quoting rule
            #   /c "exe with spaces" args  -> cmd.exe strips first+last quote,
            #      which works for paths-with-spaces (e.g. python.exe in .venv)
            #   /c "npm" args              -> cmd.exe finds "npm".cmd (NOT npm.cmd)
            #      because .cmd lookup uses the literal token including trailing "
            #
            # Fix: only quote $Exe when its path contains spaces.
            #   Paths with spaces  (python.exe) -> "/c `"$Exe`" $CmdArgs >> ..."
            #   Simple names       (npm, node)  -> "/c $Exe $CmdArgs >> ..."
            if ($Exe -match '\s') {
                $cmdLine = "/c `"$Exe`" $CmdArgs >> `"$LogFile`" 2>&1"
            } else {
                $cmdLine = "/c $Exe $CmdArgs >> `"$LogFile`" 2>&1"
            }
            $proc = Start-Process `
                -FilePath         "cmd.exe" `
                -ArgumentList     $cmdLine `
                -WorkingDirectory $WorkDir `
                -WindowStyle      Hidden `
                -PassThru
        } else {
            $proc = Start-Process `
                -FilePath         $Exe `
                -ArgumentList     $CmdArgs `
                -WorkingDirectory $WorkDir `
                -WindowStyle      Hidden `
                -PassThru
        }
        Write-Host ("  [OK]  " + $Name.PadRight(28) + "PID: " + $proc.Id)
        return $proc.Id
    } catch {
        Write-Host ("  [!!]  " + $Name.PadRight(28) + "Failed: $_")
        return 0
    }
}

# Header
Write-Host ""
Write-Host "============================================="
Write-Host "  VOD Ad Overlay System  START"
Write-Host "============================================="
Write-Host "  Python : $PythonExe"
Write-Host "  LogDir : $LogDir"
Write-Host "---------------------------------------------"

# Step 1-5: Python backend services
# NOTE: No -LogFile here. Each Python service already has TimedRotatingFileHandler
# in common/logging_setup.py that writes to storage/logs/step*.log.
# Redirecting cmd.exe stdout/stderr to the SAME file causes [Errno 13] Permission
# denied because Windows file sharing between cmd.exe append-redirect and Python's
# file handler is incompatible. WindowStyle=Hidden keeps the process backgrounded.

$pidMap["step1"] = Start-Svc `
    -Name    "Step1  Preprocessing" `
    -Exe     $PythonExe `
    -CmdArgs "-m step1_preprocessing.pipeline --consume" `
    -WorkDir $Backend

$pidMap["step2"] = Start-Svc `
    -Name    "Step2  Analysis" `
    -Exe     $PythonExe `
    -CmdArgs "-m step2_analysis.consumer" `
    -WorkDir $Backend

$pidMap["step3"] = Start-Svc `
    -Name    "Step3  Persistence" `
    -Exe     $PythonExe `
    -CmdArgs "-m step3_persistence.pipeline" `
    -WorkDir $Backend

$pidMap["step4"] = Start-Svc `
    -Name    "Step4  Scoring" `
    -Exe     $PythonExe `
    -CmdArgs "-m step4_decision.scoring" `
    -WorkDir $Backend

$pidMap["step5"] = Start-Svc `
    -Name    "Step5  API (FastAPI)" `
    -Exe     $PythonExe `
    -CmdArgs "-m step5_api.server" `
    -WorkDir $Backend

# Frontend: Next.js dev server
if (-not $SkipFrontend) {
    if (-not (Test-Path $Frontend)) {
        Write-Host "  [WW]  Frontend directory not found - skipping: $Frontend"
    } else {
        $pidMap["frontend"] = Start-Svc `
            -Name    "Frontend Next.js" `
            -Exe     "npm" `
            -CmdArgs "run dev" `
            -WorkDir $Frontend `
            -LogFile (Join-Path $LogDir "frontend.log")
    }
} else {
    Write-Host "  [>>]  Frontend skipped (-SkipFrontend flag)"
}

# Save PIDs
$pidMap | ConvertTo-Json | Set-Content -Path $PidFile -Encoding UTF8

# Done
Write-Host "---------------------------------------------"
Write-Host "  [OK]  All services started"
Write-Host ""
Write-Host "  API  : http://localhost:8000/docs"
Write-Host "  UI   : http://localhost:3000"
Write-Host ""
Write-Host "  Logs : $LogDir"
Write-Host "  Stop : .\stop.ps1"
Write-Host "  Stat : .\status.ps1"
Write-Host "============================================="
Write-Host ""
