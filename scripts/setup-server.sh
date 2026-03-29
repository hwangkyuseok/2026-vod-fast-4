#!/bin/bash
# setup-server.sh
# ─────────────────────────────────────────────────────────────────────────────
# 배포 서버에서 최초 1회 실행하는 셋업 스크립트
#
# 수행 내용:
#   1. Docker / Docker Compose 설치 확인
#   2. 필요 디렉토리 생성
#   3. 프로젝트 파일 배포
#   4. Docker 이미지 빌드
#
# 실행 방법:
#   chmod +x scripts/setup-server.sh
#   ./scripts/setup-server.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

DEPLOY_DIR="/opt/vod-ad-overlay"
SERVER_USER="${SUDO_USER:-$(whoami)}"

echo "============================================================"
echo "  VOD Ad Overlay - 서버 초기 설정"
echo "  배포 경로: $DEPLOY_DIR"
echo "============================================================"

# ── Step 1: Docker 설치 확인 ──────────────────────────────────────────────────
echo ""
echo "[1/5] Docker 설치 확인..."

if ! command -v docker &>/dev/null; then
    echo "  Docker가 설치되지 않았습니다. 설치합니다..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
    usermod -aG docker "$SERVER_USER"
    echo "  Docker 설치 완료 (로그아웃 후 재로그인 필요)"
else
    DOCKER_VER=$(docker --version)
    echo "  OK: $DOCKER_VER"
fi

if ! docker compose version &>/dev/null; then
    echo "  Docker Compose plugin 설치 중..."
    apt-get update -qq
    apt-get install -y --no-install-recommends docker-compose-plugin
fi
echo "  OK: $(docker compose version)"

# ── Step 2: 디렉토리 구조 생성 ────────────────────────────────────────────────
echo ""
echo "[2/5] 디렉토리 구조 생성..."

mkdir -p \
    "$DEPLOY_DIR/video_ads" \
    "$DEPLOY_DIR/banner_ads" \
    "$DEPLOY_DIR/storage/logs" \
    "$DEPLOY_DIR/backend/common"

echo "  생성된 디렉토리:"
echo "    $DEPLOY_DIR/video_ads     ← 광고 영상 (.mp4) 업로드 위치"
echo "    $DEPLOY_DIR/banner_ads    ← 광고 배너 (.jpg) 업로드 위치"
echo "    $DEPLOY_DIR/storage/logs  ← 로그 저장"

# ── Step 3: 프로젝트 파일 복사 ────────────────────────────────────────────────
echo ""
echo "[3/5] 프로젝트 파일 복사..."

# 이 스크립트는 프로젝트 루트에서 실행해야 함
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "  프로젝트 루트: $PROJECT_ROOT"

# 필수 파일 복사
cp -r "$PROJECT_ROOT/backend/common"                 "$DEPLOY_DIR/backend/"
cp    "$PROJECT_ROOT/backend/analyze_ad_inventory.py" "$DEPLOY_DIR/backend/"
cp    "$PROJECT_ROOT/backend/Dockerfile.analyze-ads"  "$DEPLOY_DIR/backend/"
cp    "$PROJECT_ROOT/backend/requirements.analyze-ads.txt" "$DEPLOY_DIR/backend/"
cp    "$PROJECT_ROOT/docker-compose.analyze-ads.yml"  "$DEPLOY_DIR/"

echo "  복사 완료"

# ── Step 4: docker-compose.yml 경로 수정 ─────────────────────────────────────
echo ""
echo "[4/5] docker-compose 설정 확인..."

COMPOSE_FILE="$DEPLOY_DIR/docker-compose.analyze-ads.yml"

# build.context 경로가 상대 경로 ./backend -> 절대 경로로 변경 (서버 환경)
# sed 로 context 경로 업데이트
sed -i "s|context: ./backend|context: $DEPLOY_DIR/backend|g" "$COMPOSE_FILE"

echo "  build.context 경로 업데이트: $DEPLOY_DIR/backend"

# ── Step 5: Docker 이미지 빌드 ────────────────────────────────────────────────
echo ""
echo "[5/5] Docker 이미지 빌드..."
echo "  (첫 빌드 시 CPU-only PyTorch 다운로드로 5-10분 소요될 수 있습니다)"
echo ""

cd "$DEPLOY_DIR"
docker compose -f docker-compose.analyze-ads.yml build

echo ""
echo "============================================================"
echo "  셋업 완료!"
echo "============================================================"
echo ""
echo "  광고 파일 업로드 후 아래 명령어로 분석을 시작하세요:"
echo ""
echo "  # 전체 분석 실행"
echo "  cd $DEPLOY_DIR"
echo "  docker compose -f docker-compose.analyze-ads.yml run --rm analyze-ads"
echo ""
echo "  # 10개만 테스트"
echo "  docker compose -f docker-compose.analyze-ads.yml run --rm analyze-ads --limit 10"
echo ""
echo "  # 로그 확인"
echo "  tail -f $DEPLOY_DIR/storage/logs/analyze_ad_inventory.log"
echo ""
echo "  ※ Qwen2-VL 모델(~4.5GB)은 첫 실행 시 자동 다운로드됩니다."
echo "============================================================"
