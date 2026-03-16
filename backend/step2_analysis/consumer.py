"""
Step 2 — Multimodal Analysis Service
──────────────────────────────────────
v2.0  : YOLOv8l 배치 스트리밍, Qwen2-VL 프레임 샘플링, 침묵 감지, Whisper STT
v2.1  : context_tags 생성 (침묵 역추적)
v2.2  : context_narrative 생성 (semantic 매칭)
v2.5  : Phase A 정방향 씬 분절 도입 (_generate_scene_contexts 교체)
        - dialogue_segmenter.segment_video()로 영상 전체 씬 경계 탐지
        - 각 씬 내 프레임 균등 샘플링(3~5장) + 대사 동기화
        - Qwen2-VL analyse_scene_context()로 멀티모달 씬 컨텍스트 생성
        - analysis_scene 테이블 INSERT
        - 각 침묵 구간 → 소속 씬의 narrative → analysis_audio.context_summary 할당

실행:
    python -m step2_analysis.consumer
"""

import logging
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import config, db as _db, rabbitmq as mq
from common.logging_setup import setup_logging
from step2_analysis import (
    audio_analysis,
    audio_transcription,
    dialogue_segmenter,
    vision_qwen,
    vision_yolo as vision_rcnn,  # YOLOv8l replaces Faster R-CNN
)

setup_logging("step2")
logger = logging.getLogger(__name__)


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _insert_vision_batch(job_id: str, rows: list[dict]) -> None:
    if not rows:
        return

    params = [
        (
            job_id,
            r["frame_index"],
            r["timestamp_sec"],
            r.get("safe_area_x"),
            r.get("safe_area_y"),
            r.get("safe_area_w"),
            r.get("safe_area_h"),
            r.get("object_density"),
            r.get("is_scene_cut", False),
        )
        for r in rows
    ]

    with _db.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO analysis_vision_context
                (job_id, frame_index, timestamp_sec,
                 safe_area_x, safe_area_y, safe_area_w, safe_area_h,
                 object_density, is_scene_cut)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING
            """,
            params,
        )
    logger.debug("Inserted %d vision rows for job %s", len(rows), job_id)


def _update_scene_descriptions(
    job_id: str,
    descriptions: dict[int, str],
    total_frames: int,
) -> None:
    """
    Propagate Qwen2-VL scene descriptions to all frames via range UPDATEs.

    Each sampled description covers frames from its index up to (but not
    including) the next sampled index.
    """
    if not descriptions:
        return

    sorted_indices = sorted(descriptions.keys())

    with _db.cursor() as cur:
        for i, start_idx in enumerate(sorted_indices):
            desc = descriptions[start_idx]
            if not desc:
                continue
            end_idx = (
                sorted_indices[i + 1]
                if i + 1 < len(sorted_indices)
                else total_frames
            )
            cur.execute(
                """
                UPDATE analysis_vision_context
                   SET scene_description = %s
                 WHERE job_id = %s
                   AND frame_index >= %s
                   AND frame_index < %s
                """,
                (desc, job_id, start_idx, end_idx),
            )

    logger.info(
        "Updated scene descriptions for %d segments (job %s)",
        len(sorted_indices), job_id,
    )


def _insert_audio_intervals(job_id: str, intervals: list[dict]) -> None:
    if not intervals:
        return

    params = [
        (job_id, iv["silence_start_sec"], iv["silence_end_sec"])
        for iv in intervals
    ]

    with _db.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO analysis_audio (job_id, silence_start_sec, silence_end_sec)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            params,
        )
    logger.info("Inserted %d audio silence rows for job %s", len(intervals), job_id)


def _insert_transcript(job_id: str, segments: list[dict]) -> None:
    if not segments:
        return

    params = [
        (job_id, seg["start_sec"], seg["end_sec"], seg["text"])
        for seg in segments
    ]

    with _db.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO analysis_transcript (job_id, start_sec, end_sec, text)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            params,
        )
    logger.info("Inserted %d transcript segment(s) for job %s", len(segments), job_id)


def _update_job_status(job_id: str, status: str, error: str | None = None) -> None:
    _db.execute(
        "UPDATE job_history SET status=%s, error_message=%s, updated_at=NOW() WHERE job_id=%s",
        (status, error, job_id),
    )


# ─── Phase A: 정방향 씬 분절 + 씬별 멀티모달 컨텍스트 생성 (v2.5) ──────────

def _sample_frames_for_scene(
    frame_paths: list[str],
    scene_start_sec: float,
    scene_end_sec: float,
    n: int = 4,
) -> list[str]:
    """
    씬의 시작~종료 구간에서 n장의 프레임을 균등 샘플링.

    frame_paths는 1fps JPEG 목록이므로 frame_index ≈ timestamp_sec.
    가용 프레임 수가 n보다 적으면 전부 반환.
    """
    total = len(frame_paths)
    if total == 0:
        return []

    start_idx = max(0, int(scene_start_sec * config.FRAME_EXTRACTION_FPS))
    end_idx = min(total - 1, int(scene_end_sec * config.FRAME_EXTRACTION_FPS))

    if start_idx > end_idx:
        return []

    if end_idx == start_idx:
        return [frame_paths[start_idx]]

    count = min(n, end_idx - start_idx + 1)
    # 균등 분포: linspace로 float 인덱스 계산 후 정수화·중복 제거
    import numpy as np
    float_indices = list(
        float(x) for x in
        [start_idx + (end_idx - start_idx) * k / max(1, count - 1) for k in range(count)]
    )
    indices = sorted({min(total - 1, int(round(fi))) for fi in float_indices})
    return [frame_paths[i] for i in indices]


def _insert_scene_context(
    job_id: str,
    scene_start_sec: float,
    scene_end_sec: float,
    context_narrative: str,
) -> None:
    """analysis_scene 테이블에 씬 컨텍스트를 저장 (ON CONFLICT DO NOTHING)."""
    _db.execute(
        """
        INSERT INTO analysis_scene
            (job_id, scene_start_sec, scene_end_sec, context_narrative)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (job_id, scene_start_sec) DO UPDATE
            SET scene_end_sec     = EXCLUDED.scene_end_sec,
                context_narrative = EXCLUDED.context_narrative
        """,
        (job_id, scene_start_sec, scene_end_sec, context_narrative or None),
    )


def _assign_scene_context_to_silences(
    job_id: str,
    scenes: list[dict],
) -> None:
    """
    각 침묵 구간을 소속 씬에 귀속시키고 해당 씬의 context_narrative를
    analysis_audio.context_summary로 UPDATE.

    매핑 전략:
      1. silence_start_sec가 씬의 [scene_start, scene_end) 범위에 포함되면 해당 씬.
      2. 어느 씬에도 포함되지 않으면 silence 직전에 종료된 씬(가장 가까운 이전 씬).
    """
    if not scenes:
        return

    silence_rows = _db.fetchall(
        "SELECT silence_start_sec FROM analysis_audio WHERE job_id = %s ORDER BY silence_start_sec",
        (job_id,),
    )

    updated = 0
    for row in silence_rows:
        start = float(row["silence_start_sec"])
        narrative = ""

        # 포함 씬 탐색
        for scene in scenes:
            if scene["scene_start_sec"] <= start < scene["scene_end_sec"]:
                narrative = scene.get("context_narrative") or ""
                break

        # 미탐지 시 직전 씬 사용
        if not narrative:
            for scene in reversed(scenes):
                if scene["scene_start_sec"] <= start:
                    narrative = scene.get("context_narrative") or ""
                    break

        if narrative:
            _db.execute(
                """
                UPDATE analysis_audio
                   SET context_summary = %s
                 WHERE job_id = %s
                   AND silence_start_sec = %s
                """,
                (narrative, job_id, start),
            )
            updated += 1

    logger.info(
        "[%s] Assigned scene context to %d/%d silence interval(s).",
        job_id, updated, len(silence_rows),
    )


def _generate_scene_contexts(
    job_id: str,
    transcript_segments: list[dict],
    frame_paths: list[str],
    total_duration_sec: float,
    visual_cut_times: list[float] | None = None,
) -> None:
    """
    Phase A 정방향 씬 분석 오케스트레이터. (v2.5 — _generate_context_tags 교체)

    1. segment_video(): 대사 임베딩 기반 씬 경계 자동 탐지 (전체 영상)
    2. 각 씬마다:
       a. 해당 구간 대사 텍스트 수집 (Whisper)
       b. 프레임 균등 샘플링 (3~5장) — 시각/음성 동기화
       c. analyse_scene_context(): Qwen2-VL 멀티모달 씬 narrative 생성
       d. analysis_scene 테이블 INSERT
    3. 각 침묵 구간 → 소속 씬 narrative → analysis_audio.context_summary UPDATE
    """
    logger.info(
        "[%s] Phase A: scene segmentation + context generation ...",
        job_id,
    )

    scenes = dialogue_segmenter.segment_video(
        transcript_segments=transcript_segments,
        total_duration_sec=total_duration_sec,
        visual_cut_times=visual_cut_times,
    )
    logger.info("[%s] Detected %d scene(s).", job_id, len(scenes))

    for idx, scene in enumerate(scenes):
        s_start = float(scene["scene_start_sec"])
        s_end   = float(scene["scene_end_sec"])

        # ── 해당 씬 구간의 대사 텍스트 ──────────────────────────────────────
        transcript_text = " ".join(
            seg["text"]
            for seg in transcript_segments
            if s_start <= float(seg.get("start_sec", 0)) < s_end
        ).strip()

        # ── 동적 프레임 샘플링 (씬 구간 내 균등 4장) ────────────────────────
        sampled_frames = _sample_frames_for_scene(
            frame_paths, s_start, s_end, n=4
        )

        # ── Qwen2-VL 멀티모달 씬 컨텍스트 생성 ─────────────────────────────
        narrative = ""
        try:
            narrative = vision_qwen.analyse_scene_context(
                frame_paths=sampled_frames,
                transcript_text=transcript_text,
                scene_start_sec=s_start,
                scene_end_sec=s_end,
            )
        except Exception as exc:
            logger.warning(
                "[%s] Scene %d context generation failed [%.1f-%.1f]: %s",
                job_id, idx + 1, s_start, s_end, exc,
            )
            narrative = ""

        scene["context_narrative"] = narrative
        _insert_scene_context(job_id, s_start, s_end, narrative)

        logger.info(
            "[%s] Scene %d/%d [%.1f-%.1f] | frames=%d | narrative=%d chars",
            job_id, idx + 1, len(scenes), s_start, s_end,
            len(sampled_frames), len(narrative),
        )

    # ── 침묵 구간에 씬 narrative 할당 ───────────────────────────────────────
    _assign_scene_context_to_silences(job_id, scenes)
    logger.info("[%s] Phase A complete.", job_id)


# ─── Idempotency ─────────────────────────────────────────────────────────────

def _already_processed(job_id: str) -> bool:
    """
    Return True if Step-2 results already exist for this job.

    RabbitMQ may redeliver a message when the consumer_timeout (default 30 min)
    expires before the ack is sent — even if processing completed successfully.
    Checking the DB lets us skip the multi-hour re-analysis safely.
    """
    row = _db.fetchone(
        "SELECT COUNT(*) AS cnt FROM analysis_vision_context WHERE job_id = %s",
        (job_id,),
    )
    return bool(row and row.get("cnt", 0) > 0)


# ─── Main analysis logic ──────────────────────────────────────────────────────

def run(job_id: str) -> None:
    # ── Idempotency guard ─────────────────────────────────────────────────────
    if _already_processed(job_id):
        logger.warning(
            "[%s] Vision context already exists — redelivered message, "
            "skipping Step-2. Publishing to Step-3 again.",
            job_id,
        )
        mq.publish(config.QUEUE_STEP3, {"job_id": job_id})
        return

    _update_job_status(job_id, "analysing")
    try:
        info = _db.fetchone(
            "SELECT * FROM video_preprocessing_info WHERE job_id = %s",
            (job_id,),
        )
        if info is None:
            raise ValueError(f"No preprocessing info for job_id={job_id}")

        frame_dir   = Path(info["frame_dir_path"])
        frame_paths = sorted(str(p) for p in frame_dir.glob("*.jpg"))

        if not frame_paths:
            raise FileNotFoundError(f"No frames found in {frame_dir}")

        total_frames       = len(frame_paths)
        total_duration_sec = float(info["duration_sec"])

        logger.info("[%s] Total frames: %d | Duration: %.1fs", job_id, total_frames, total_duration_sec)

        # ── Vision: YOLOv8l (streaming batch inserts) ─────────────────────────
        logger.info("[%s] Starting YOLOv8l analysis ...", job_id)
        frames_inserted = 0

        def _on_rcnn_batch(batch: list[dict]) -> None:
            nonlocal frames_inserted
            _insert_vision_batch(job_id, batch)
            frames_inserted += len(batch)

        vision_rcnn.analyse_frames(frame_paths, on_batch=_on_rcnn_batch)
        logger.info("[%s] YOLO complete — %d frames streamed to DB", job_id, frames_inserted)

        # ── Vision: Qwen2-VL (fixed-interval sampling → range UPDATE) ─────────
        # 이 패스는 analysis_vision_context.scene_description 채우기용.
        # Phase A의 씬 단위 멀티프레임 분석과 별개.
        logger.info("[%s] Starting Qwen2-VL fixed-interval sampling ...", job_id)
        qwen_descriptions = vision_qwen.analyse_frames(frame_paths)
        _update_scene_descriptions(job_id, qwen_descriptions, total_frames)

        # ── Audio: silence detection ───────────────────────────────────────────
        logger.info("[%s] Starting audio silence detection ...", job_id)
        audio_intervals = audio_analysis.detect_silence(info["audio_path"])
        _insert_audio_intervals(job_id, audio_intervals)

        # ── Audio: speech-to-text transcript ──────────────────────────────────
        logger.info("[%s] Starting Whisper transcription ...", job_id)
        transcript_segments = audio_transcription.transcribe(info["audio_path"])
        _insert_transcript(job_id, transcript_segments)

        # ── Phase A: 정방향 씬 분절 + 멀티모달 컨텍스트 생성 (v2.5/v2.8) ───────
        logger.info("[%s] Starting Phase A: scene segmentation + context ...", job_id)
        visual_cuts = info.get("scene_cut_times") or []
        _generate_scene_contexts(
            job_id=job_id,
            transcript_segments=transcript_segments,
            frame_paths=frame_paths,
            total_duration_sec=total_duration_sec,
            visual_cut_times=visual_cuts if isinstance(visual_cuts, list) else [],
        )

        _update_job_status(job_id, "persisting")
        mq.publish(config.QUEUE_STEP3, {"job_id": job_id})
        logger.info("[%s] Step-2 complete → published to %s", job_id, config.QUEUE_STEP3)

    except Exception as exc:
        _update_job_status(job_id, "failed", str(exc))
        logger.exception("[%s] Step-2 failed: %s", job_id, exc)
        raise


# ─── Consumer entry-point ─────────────────────────────────────────────────────

def _on_message(payload: dict) -> None:
    run(payload["job_id"])


if __name__ == "__main__":
    # ack_early=True: RabbitMQ consumer_timeout 만료 전 ACK 선발송.
    # _already_processed() 가드로 재실행 안전성 보장.
    mq.consume(config.QUEUE_STEP2, _on_message, ack_early=True)
