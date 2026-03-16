-- ============================================================
-- VOD Dynamic Ad Overlay System — Database Schema
-- ============================================================

-- ─── 카탈로그 테이블만 재생성 (잡/분석 데이터는 보존) ──────
-- ad_inventory : 파일 기반 카탈로그 → DROP 후 재생성 안전
-- decision_result : ad_inventory FK 참조 → 함께 재생성
-- job_history / preprocessing / analysis 테이블은 건드리지 않음
DROP TABLE IF EXISTS decision_result CASCADE;
DROP TABLE IF EXISTS ad_inventory    CASCADE;

-- ─── 1. Job History ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS job_history (
    id              SERIAL PRIMARY KEY,
    job_id          UUID        NOT NULL UNIQUE,
    status          VARCHAR(50) NOT NULL DEFAULT 'pending',
    -- pending | preprocessing | analysing | persisting | deciding | complete | failed
    input_video_path TEXT       NOT NULL,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_history_job_id ON job_history(job_id);
CREATE INDEX IF NOT EXISTS idx_job_history_status  ON job_history(status);

-- ─── 2. Video Preprocessing Info ────────────────────────────
CREATE TABLE IF NOT EXISTS video_preprocessing_info (
    id               SERIAL PRIMARY KEY,
    job_id           UUID    NOT NULL REFERENCES job_history(job_id) ON DELETE CASCADE,
    original_video_path TEXT NOT NULL,
    audio_path       TEXT    NOT NULL,
    frame_dir_path   TEXT    NOT NULL,
    duration_sec     FLOAT   NOT NULL,
    fps              FLOAT   NOT NULL,
    width            INTEGER NOT NULL,
    height           INTEGER NOT NULL,
    total_frames     INTEGER NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vpi_job_id ON video_preprocessing_info(job_id);

-- ─── 3. Vision / Context Analysis (per-frame) ───────────────
CREATE TABLE IF NOT EXISTS analysis_vision_context (
    id               SERIAL PRIMARY KEY,
    job_id           UUID    NOT NULL REFERENCES job_history(job_id) ON DELETE CASCADE,
    frame_index      INTEGER NOT NULL,
    timestamp_sec    FLOAT   NOT NULL,
    -- Safe area (largest unoccupied rectangle)
    safe_area_x      INTEGER,
    safe_area_y      INTEGER,
    safe_area_w      INTEGER,
    safe_area_h      INTEGER,
    -- Object density: total bbox area / frame area (0.0 – 1.0)
    object_density   FLOAT,
    -- Qwen2-VL scene description
    scene_description TEXT,
    -- Scene cut flag (set on first frame after a cut)
    is_scene_cut     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_avc_job_id        ON analysis_vision_context(job_id);
CREATE INDEX IF NOT EXISTS idx_avc_timestamp_sec ON analysis_vision_context(job_id, timestamp_sec);

-- Dedup any existing duplicates before creating unique index
-- (keeps the row with the lowest id for each job_id + frame_index pair)
DELETE FROM analysis_vision_context a
      USING analysis_vision_context b
      WHERE a.id > b.id
        AND a.job_id      = b.job_id
        AND a.frame_index = b.frame_index;

-- Unique constraint: one row per (job, frame).
-- Enables ON CONFLICT DO NOTHING to work correctly on re-runs.
CREATE UNIQUE INDEX IF NOT EXISTS uq_avc_job_frame
    ON analysis_vision_context (job_id, frame_index);

-- ─── 4. Audio Analysis (silence intervals) ──────────────────
CREATE TABLE IF NOT EXISTS analysis_audio (
    id                SERIAL PRIMARY KEY,
    job_id            UUID  NOT NULL REFERENCES job_history(job_id) ON DELETE CASCADE,
    silence_start_sec FLOAT NOT NULL,
    silence_end_sec   FLOAT NOT NULL,
    -- Computed column: silence duration
    duration_sec      FLOAT GENERATED ALWAYS AS (silence_end_sec - silence_start_sec) STORED,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_aa_job_id ON analysis_audio(job_id);

-- Dedup any existing duplicate silence intervals before creating unique index
DELETE FROM analysis_audio a
      USING analysis_audio b
      WHERE a.id > b.id
        AND a.job_id            = b.job_id
        AND a.silence_start_sec = b.silence_start_sec
        AND a.silence_end_sec   = b.silence_end_sec;

-- Unique constraint: one row per (job, silence window).
-- Prevents duplicate inserts when Step-2 is retried or redelivered.
CREATE UNIQUE INDEX IF NOT EXISTS uq_aa_job_silence
    ON analysis_audio (job_id, silence_start_sec, silence_end_sec);

-- ─── 5. Audio Transcript (Whisper STT segments) ────────────
CREATE TABLE IF NOT EXISTS analysis_transcript (
    id          SERIAL PRIMARY KEY,
    job_id      UUID  NOT NULL REFERENCES job_history(job_id) ON DELETE CASCADE,
    start_sec   FLOAT NOT NULL,
    end_sec     FLOAT NOT NULL,
    text        TEXT  NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_at_job_id ON analysis_transcript(job_id);
CREATE INDEX IF NOT EXISTS idx_at_time   ON analysis_transcript(job_id, start_sec, end_sec);

DELETE FROM analysis_transcript a
      USING analysis_transcript b
      WHERE a.id > b.id
        AND a.job_id    = b.job_id
        AND a.start_sec = b.start_sec
        AND a.end_sec   = b.end_sec;

CREATE UNIQUE INDEX IF NOT EXISTS uq_at_job_segment
    ON analysis_transcript (job_id, start_sec, end_sec);

-- ─── 6. Ad Inventory ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ad_inventory (
    id               SERIAL PRIMARY KEY,
    ad_id            VARCHAR(200) NOT NULL UNIQUE,
    ad_name          TEXT,
    ad_type          VARCHAR(50)  NOT NULL,   -- 'video_clip' | 'banner'
    resource_path    TEXT         NOT NULL,
    duration_sec     FLOAT,                   -- NULL for images (→ use default display time)
    -- v2.5: 4차원 서술형 광고 내러티브 (Category / Audience / Core Message / Ad Vibe)
    -- analyze_ad_narrative.py 실행으로 채워짐.
    target_narrative TEXT,
    width            INTEGER,
    height           INTEGER,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ad_inventory_ad_id ON ad_inventory(ad_id);


-- ─── 7. Scene Context (v2.5) ────────────────────────────────
-- 영상 전체를 의미 단위 씬으로 분절한 결과.
-- consumer.py Phase A에서 INSERT.
-- 각 silence interval은 자신이 속한 scene의 context_narrative를
-- analysis_audio.context_summary로 할당받음 → 하위 단계 무변경.
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

-- ─── 8. Decision Result ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS decision_result (
    id                    SERIAL PRIMARY KEY,
    job_id                UUID         NOT NULL REFERENCES job_history(job_id) ON DELETE CASCADE,
    ad_id                 VARCHAR(200) NOT NULL REFERENCES ad_inventory(ad_id),
    overlay_start_time_sec FLOAT       NOT NULL,
    overlay_duration_sec   FLOAT       NOT NULL,
    coordinates_x         INTEGER,
    coordinates_y         INTEGER,
    coordinates_w         INTEGER,
    coordinates_h         INTEGER,
    score                 INTEGER      NOT NULL,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dr_job_id ON decision_result(job_id);
CREATE INDEX IF NOT EXISTS idx_dr_score  ON decision_result(job_id, score DESC);
