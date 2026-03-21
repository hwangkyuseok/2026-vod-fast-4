"""
migrate_add_cross_encoder_labels.py — Cross-Encoder 학습 데이터 테이블 생성
──────────────────────────────────────────────────────────────────────────
cross_encoder_labels 테이블 신규 생성.
Gemini 라벨링 결과를 저장하여 Cross-Encoder Fine-tuning에 사용.

안전: 테이블이 이미 존재하면 아무것도 하지 않음 (멱등).
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import db as _db
from common.logging_setup import setup_logging

setup_logging("migrate_cross_encoder_labels")
logger = logging.getLogger(__name__)


def run() -> None:
    logger.info("Starting migration: create cross_encoder_labels ...")

    _db.execute(
        """
        CREATE TABLE IF NOT EXISTS cross_encoder_labels (
            id                SERIAL PRIMARY KEY,
            scene_id          INTEGER      NOT NULL,   -- analysis_scene.id
            ad_id             VARCHAR(200) NOT NULL,   -- ad_inventory.ad_id
            context_narrative TEXT         NOT NULL,   -- 씬 서술문 (입력 피처)
            target_narrative  TEXT         NOT NULL,   -- 광고 서술문 (입력 피처)
            gemini_score      FLOAT        NOT NULL,   -- Gemini 관련도 점수 (0.0~1.0)
            label             VARCHAR(20)  NOT NULL,   -- 'train' | 'review' | 'human_check'
            created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            UNIQUE (scene_id, ad_id)
        )
        """
    )

    _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_cel_label ON cross_encoder_labels(label)"
    )
    _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_cel_score ON cross_encoder_labels(gemini_score)"
    )

    logger.info("Migration complete: cross_encoder_labels created.")


if __name__ == "__main__":
    run()
