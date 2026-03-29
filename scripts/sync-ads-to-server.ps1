# sync-ads-to-server.ps1
# ─────────────────────────────────────────────────────────────────────────────
# 로컬 Windows의 광고 파일을 배포 서버로 전송한다.
#
# 사전 준비:
#   1. SSH 키 또는 비밀번호 인증으로 서버 접근 가능해야 함
#   2. 서버에 scp/rsync 사용 가능 (OpenSSH 설치 필요)
#   3. Windows에서 OpenSSH 클라이언트 활성화:
#      설정 → 앱 → 선택적 기능 → OpenSSH 클라이언트
#
# 사용법:
#   .\scripts\sync-ads-to-server.ps1 -ServerHost <IP> -LocalVideoDir <경로> -LocalBannerDir <경로>
#   .\scripts\sync-ads-to-server.ps1 -ServerHost <IP> -ServerUser <계정> -SshKeyPath "C:\Users\<user>\.ssh\id_rsa"
#   .\scripts\sync-ads-to-server.ps1 -ServerHost <IP> -LocalVideoDir <경로> -DryRun
# ─────────────────────────────────────────────────────────────────────────────

param(
    [Parameter(Mandatory)][string]$ServerHost,         # 서버 IP 또는 hostname (필수)
    [string]$ServerUser    = "root",
    [string]$SshKeyPath    = "",                       # 비어있으면 비밀번호 인증
    [string]$LocalVideoDir  = "",                      # 광고 영상 디렉토리 (미지정 시 아래 안내)
    [string]$LocalBannerDir = "",                      # 광고 배너 디렉토리 (미지정 시 아래 안내)
    [switch]$DryRun                                    # 전송 없이 목록만 확인
)

# ── 로컬 경로 미지정 시 안내 ──────────────────────────────────────────────────
if (-not $LocalVideoDir) {
    Write-Host "[ERROR] -LocalVideoDir 파라미터를 지정하세요." -ForegroundColor Red
    Write-Host "  예) .\sync-ads-to-server.ps1 -ServerHost <IP> -LocalVideoDir 'C:\path\to\video'" -ForegroundColor Gray
    exit 1
}
if (-not $LocalBannerDir) {
    Write-Host "[ERROR] -LocalBannerDir 파라미터를 지정하세요." -ForegroundColor Red
    Write-Host "  예) .\sync-ads-to-server.ps1 -ServerHost <IP> -LocalBannerDir 'C:\path\to\banner'" -ForegroundColor Gray
    exit 1
}

# ── 서버 경로 ─────────────────────────────────────────────────────────────────
$ServerVideoDir  = "/opt/vod-ad-overlay/video_ads"
$ServerBannerDir = "/opt/vod-ad-overlay/banner_ads"
$ServerLogsDir   = "/opt/vod-ad-overlay/storage/logs"

# ── SSH 옵션 ──────────────────────────────────────────────────────────────────
$SshOpts = @("-o", "StrictHostKeyChecking=no", "-o", "BatchMode=no")
if ($SshKeyPath -ne "") {
    $SshOpts += @("-i", $SshKeyPath)
}
$ScpOpts = $SshOpts

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  VOD Ad Overlay - 광고 파일 서버 동기화" -ForegroundColor Cyan
Write-Host "  서버: $ServerUser@$ServerHost" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# ── Step 1: 서버 디렉토리 생성 ────────────────────────────────────────────────
Write-Host "`n[1/3] 서버 디렉토리 생성 중..." -ForegroundColor Yellow

$MkdirCmd = "mkdir -p $ServerVideoDir $ServerBannerDir $ServerLogsDir && echo 'dirs OK'"
$SshArgs = $SshOpts + @("$ServerUser@$ServerHost", $MkdirCmd)

if (-not $DryRun) {
    $result = & ssh @SshArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: SSH 연결 실패. 서버 접근 정보를 확인하세요." -ForegroundColor Red
        Write-Host "  $result" -ForegroundColor Red
        exit 1
    }
    Write-Host "  디렉토리 생성 완료" -ForegroundColor Green
} else {
    Write-Host "  [DRY-RUN] ssh $ServerUser@$ServerHost '$MkdirCmd'" -ForegroundColor Gray
}

# ── Step 2: 광고 영상 전송 (*.mp4) ────────────────────────────────────────────
Write-Host "`n[2/3] 광고 영상 전송 중 ($LocalVideoDir)..." -ForegroundColor Yellow

$VideoFiles = Get-ChildItem -Path $LocalVideoDir -Filter "*.mp4" -File
Write-Host "  영상 파일 수: $($VideoFiles.Count)" -ForegroundColor White

if ($VideoFiles.Count -eq 0) {
    Write-Host "  WARNING: 영상 파일 없음 ($LocalVideoDir)" -ForegroundColor Yellow
} elseif (-not $DryRun) {
    foreach ($file in $VideoFiles) {
        Write-Host "  전송 중: $($file.Name)" -ForegroundColor Gray
        $ScpArgs = $ScpOpts + @($file.FullName, "$ServerUser@${ServerHost}:$ServerVideoDir/")
        & scp @ScpArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  ERROR: $($file.Name) 전송 실패" -ForegroundColor Red
        }
    }
    Write-Host "  영상 전송 완료" -ForegroundColor Green
} else {
    foreach ($file in $VideoFiles) {
        Write-Host "  [DRY-RUN] scp $($file.FullName) $ServerUser@${ServerHost}:$ServerVideoDir/" -ForegroundColor Gray
    }
}

# ── Step 3: 광고 배너 전송 (*.jpg) ────────────────────────────────────────────
Write-Host "`n[3/3] 광고 배너 전송 중 ($LocalBannerDir)..." -ForegroundColor Yellow

$BannerFiles = Get-ChildItem -Path $LocalBannerDir -Filter "*.jpg" -File
Write-Host "  배너 파일 수: $($BannerFiles.Count)" -ForegroundColor White

if ($BannerFiles.Count -eq 0) {
    Write-Host "  WARNING: 배너 파일 없음 ($LocalBannerDir)" -ForegroundColor Yellow
} elseif (-not $DryRun) {
    foreach ($file in $BannerFiles) {
        Write-Host "  전송 중: $($file.Name)" -ForegroundColor Gray
        $ScpArgs = $ScpOpts + @($file.FullName, "$ServerUser@${ServerHost}:$ServerBannerDir/")
        & scp @ScpArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  ERROR: $($file.Name) 전송 실패" -ForegroundColor Red
        }
    }
    Write-Host "  배너 전송 완료" -ForegroundColor Green
} else {
    foreach ($file in $BannerFiles) {
        Write-Host "  [DRY-RUN] scp $($file.FullName) $ServerUser@${ServerHost}:$ServerBannerDir/" -ForegroundColor Gray
    }
}

# ── 완료 요약 ─────────────────────────────────────────────────────────────────
Write-Host "`n============================================================" -ForegroundColor Cyan
if ($DryRun) {
    Write-Host "  [DRY-RUN 완료] 실제 전송은 수행되지 않았습니다." -ForegroundColor Yellow
} else {
    Write-Host "  전송 완료!" -ForegroundColor Green
    Write-Host "  서버 경로:" -ForegroundColor White
    Write-Host "    영상: $ServerUser@${ServerHost}:$ServerVideoDir" -ForegroundColor White
    Write-Host "    배너: $ServerUser@${ServerHost}:$ServerBannerDir" -ForegroundColor White
}
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "다음 단계: 서버에서 Docker 컨테이너 실행" -ForegroundColor White
Write-Host "  ssh $ServerUser@$ServerHost" -ForegroundColor Gray
Write-Host "  cd /opt/vod-ad-overlay" -ForegroundColor Gray
Write-Host "  docker compose -f docker-compose.analyze-ads.yml run --rm analyze-ads" -ForegroundColor Gray
