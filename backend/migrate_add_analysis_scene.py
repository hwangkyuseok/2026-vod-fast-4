"""
migrate_add_analysis_scene.py — v2.5 DB 마이그레이션
──────────────────────────────────────────────────────
analysis_scene 테이블 생성.

역할:
  - 영상 전체를 의미 단위 씬으로 분절한 결과 저장
  - 각 씬의 시작/종료 시각 + Qwen2-VL 생성 context_narrative 보관
  - consumer.py Step-2 Phase A에서 INSERT
  - 각 침묵 구간(analysis_audio)은 자신이 속한 씬의 context_narrative를
    context_summary로 할당받음 → Step-3/4가 별도 변경 없이 동일 컬럼 사용 가능

안전: 테이블이 이미 존재하면 아무것도 하지 않음 (멱등).
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import db as _db
from common.logging_setup import setup_logging

setup_logging("migrate_analysis_scene")
logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS analysis_scene (
    id               SERIAL PRIMARY KEY,
    job_id           UUID  NOT NULL REFERENCES job_history(job_id) ON DELETE CASCADE,
    scene_start_sec  FLOAT NOT NULL,
    scene_end_sec    FLOAT NOT NULL,
    context_narrative TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_as_job_id
    ON analysis_scene (job_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_as_job_scene_start
    ON analysis_scene (job_id, scene_start_sec);
"""


def run() -> None:
    logger.info("Starting migration: create analysis_scene table ...")

    row = _db.fetchone(
        """
        SELECT table_name
          FROM information_schema.tables
         WHERE table_name = 'analysis_scene'
           AND table_schema = 'public'
        """
    )

    if row:
        logger.info("Table analysis_scene already exists — skipping.")
        return

    # psycopg2 executemany는 DDL에 사용 불가 → cursor 직접 사용
    with _db.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)

    logger.info("Migration complete: analysis_scene table created.")


if __name__ == "__main__":
    run()
