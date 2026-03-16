#!/bin/bash
# setup-server-narrative.sh
# ─────────────────────────────────────────────────────────────────────────────
# 서버(121.167.223.17)에서 analyze-narrative 컨테이너 초기 배포 스크립트
#
# 수행 내용:
#   1. 필요 디렉토리 확인
#   2. Docker 이미지 빌드
#
# 실행 방법 (서버에서):
#   chmod +x setup-server-narrative.sh
#   ./setup-server-narrative.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

DEPLOY_DIR="/app/Docker/analyze-narrative"

echo "============================================================"
echo "  VOD Ad Narrative Analysis - 서버 초기 설정"
echo "  배포 경로: $DEPLOY_DIR"
echo "============================================================"

# ── Step 1: 디렉토리 확인 ────────────────────────────────────────────────────
echo ""
echo "[1/2] 배포 디렉토리 확인..."

if [ ! -d "$DEPLOY_DIR" ]; then
    echo "  오류: $DEPLOY_DIR 디렉토리가 없습니다."
    echo "  먼저 서버에 파일을 업로드하세요."
    exit 1
fi

echo "  OK: $DEPLOY_DIR"

# ── Step 2: Docker 이미지 빌드 ────────────────────────────────────────────────
echo ""
echo "[2/2] Docker 이미지 빌드..."
echo "  (첫 빌드 시 CPU-only PyTorch 다운로드로 5-10분 소요될 수 있습니다)"
echo ""

cd "$DEPLOY_DIR"
docker-compose -f docker-compose.analyze-narrative.yml build

echo ""
echo "============================================================"
echo "  빌드 완료! 아래 명령어로 분석을 시작하세요:"
echo "============================================================"
echo ""
echo "  # 미처리 광고 목록 확인 (dry-run)"
echo "  docker-compose -f docker-compose.analyze-narrative.yml run --rm analyze-narrative --dry-run"
echo ""
echo "  # 10개만 테스트"
echo "  docker-compose -f docker-compose.analyze-narrative.yml run --rm analyze-narrative --limit 10"
echo ""
echo "  # 전체 분석 실행"
echo "  docker-compose -f docker-compose.analyze-narrative.yml run --rm analyze-narrative"
echo ""
echo "  # 로그 확인"
echo "  tail -f /app/HelloVision/data/logs/analyze_ad_narrative.log"
echo ""
echo "  ※ Qwen2-VL 모델(~4.5GB)은 첫 실행 시 자동 다운로드됩니다."
echo "============================================================"
