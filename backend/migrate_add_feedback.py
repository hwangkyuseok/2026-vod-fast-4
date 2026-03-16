"""
migrate_add_feedback.py
────────────────────────────────────────────────────────────────
레이블 데이터 수집을 위한 DB 마이그레이션 스크립트

변경 내용:
  1. decision_result 테이블에 CatBoost 피처 컬럼 3개 추가
       - similarity_score  : context_narrative ↔ target_narrative 코사인 유사도
       - scene_duration_sec: 해당 씬의 전체 길이 (초)
       - avg_density       : 최적 윈도우 내 평균 object_density
  2. ad_placement_feedback 테이블 신규 생성
       - 사용자(👍/👎) 또는 자동 평가 라벨 저장
       - 향후 CatBoost 학습 데이터로 활용

실행:
    python migrate_add_feedback.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import db as _db
from common.logging_setup import setup_logging

setup_logging("migrate_feedback")

SQL = """
-- ── 1. decision_result에 피처 컬럼 추가 ────────────────────────────────────
-- 이미 컬럼이 있으면 무시 (IF NOT EXISTS는 ALTER TABLE에서 PostgreSQL 9.6+부터 지원 안 함
--  → DO $$ 블록으로 안전 처리)

DO $$
BEGIN
    -- similarity_score: 1차 필터에서 사용한 코사인 유사도 (0.0 ~ 1.0)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'decision_result' AND column_name = 'similarity_score'
    ) THEN
        ALTER TABLE decision_result ADD COLUMN similarity_score FLOAT;
    END IF;

    -- scene_duration_sec: 씬 길이 (광고 길이 대비 여유 공간 파악용)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'decision_result' AND column_name = 'scene_duration_sec'
    ) THEN
        ALTER TABLE decision_result ADD COLUMN scene_duration_sec FLOAT;
    END IF;

    -- avg_density: 최적 윈도우 내 평균 객체 밀도 (0.0 ~ 1.0)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'decision_result' AND column_name = 'avg_density'
    ) THEN
        ALTER TABLE decision_result ADD COLUMN avg_density FLOAT;
    END IF;
END $$;


-- ── 2. ad_placement_feedback 테이블 생성 ────────────────────────────────────
CREATE TABLE IF NOT EXISTS ad_placement_feedback (
    id              SERIAL PRIMARY KEY,
    decision_id     INTEGER      NOT NULL REFERENCES decision_result(id) ON DELETE CASCADE,

    -- 라벨: -1=부적합, 0=보통, 1=적합
    label           SMALLINT     NOT NULL CHECK (label IN (-1, 0, 1)),

    -- 라벨 출처: 'user'(프론트 버튼), 'auto'(자동 평가 스크립트)
    source          VARCHAR(20)  NOT NULL DEFAULT 'user',

    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- decision_id 중복 제출 방지 (한 배치당 라벨 1개)
CREATE UNIQUE INDEX IF NOT EXISTS uq_feedback_decision
    ON ad_placement_feedback (decision_id);

CREATE INDEX IF NOT EXISTS idx_feedback_label
    ON ad_placement_feedback (label);
"""


def main() -> None:
    print("▶ 마이그레이션 시작...")
    _db.execute(SQL)
    print("✓ decision_result 피처 컬럼 추가 완료")
    print("✓ ad_placement_feedback 테이블 생성 완료")
    print("▶ 마이그레이션 완료")


if __name__ == "__main__":
    main()
