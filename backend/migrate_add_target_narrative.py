"""
migrate_add_target_narrative.py — v2.5 DB 마이그레이션
────────────────────────────────────────────────────────
ad_inventory 테이블에 target_narrative TEXT 컬럼 추가.

기존 target_mood 배열은 하위 호환성을 위해 유지하며 삭제하지 않음.
target_narrative: Qwen2-VL 4차원 분석 결과를 담은 단일 서술문
  (Category / Target Audience / Core Message / Ad Vibe)

안전: 컬럼이 이미 존재하면 아무것도 하지 않음 (멱등).
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import db as _db
from common.logging_setup import setup_logging

setup_logging("migrate_target_narrative")
logger = logging.getLogger(__name__)


def run() -> None:
    logger.info("Starting migration: add target_narrative to ad_inventory ...")

    # 컬럼 존재 여부 확인
    row = _db.fetchone(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_name   = 'ad_inventory'
           AND column_name  = 'target_narrative'
        """
    )

    if row:
        logger.info("Column target_narrative already exists — skipping.")
        return

    _db.execute(
        "ALTER TABLE ad_inventory ADD COLUMN target_narrative TEXT"
    )

    logger.info("Migration complete: target_narrative TEXT added to ad_inventory.")


if __name__ == "__main__":
    run()
