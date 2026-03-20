"""
Step 2-B — Audio Analysis (Silence Detection + Whisper STT)
─────────────────────────────────────────────────────────────
v2.13: Step2 분리 — 오디오 분석 전용 컨테이너
       - 침묵 구간 감지 → analysis_audio INSERT
       - Whisper STT → analysis_transcript INSERT
       - 완료 시 step2b_done=TRUE, QUEUE_STEP2_GATE 발행

Consumes from QUEUE_STEP2B.
"""

import logging
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import config, db as _db, rabbitmq as mq
from common.logging_setup import setup_logging
from step2_analysis import audio_analysis, audio_transcription

setup_logging("step2b")
logger = logging.getLogger(__name__)


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _update_job_status(job_id: str, status: str, error: str | None = None) -> None:
    _db.execute(
        "UPDATE job_history SET status=%s, error_message=%s, updated_at=NOW() WHERE job_id=%s",
        (status, error, job_id),
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


def _already_processed(job_id: str) -> bool:
    row = _db.fetchone(
        "SELECT COUNT(*) AS cnt FROM analysis_audio WHERE job_id = %s",
        (job_id,),
    )
    return bool(row and row.get("cnt", 0) > 0)


# ─── Main run function ────────────────────────────────────────────────────────

def run(job_id: str) -> None:
    if _already_processed(job_id):
        logger.warning(
            "[%s] Audio analysis already exists — redelivered message, skipping. "
            "Setting step2b_done=TRUE and publishing to gate.",
            job_id,
        )
        _db.execute(
            "UPDATE job_history SET step2b_done=TRUE WHERE job_id=%s",
            (job_id,),
        )
        mq.publish(config.QUEUE_STEP2_GATE, {"job_id": job_id})
        return

    # 재처리 시작 — 플래그 리셋
    _db.execute(
        "UPDATE job_history SET step2b_done=FALSE WHERE job_id=%s",
        (job_id,),
    )
    _update_job_status(job_id, "analysing")
    try:
        info = _db.fetchone(
            "SELECT * FROM video_preprocessing_info WHERE job_id = %s",
            (job_id,),
        )
        if info is None:
            raise ValueError(f"No preprocessing info for job_id={job_id}")

        audio_path = info["audio_path"]

        # ── 침묵 감지 ─────────────────────────────────────────────────────────
        logger.info("[%s] Starting audio silence detection ...", job_id)
        audio_intervals = audio_analysis.detect_silence(audio_path)
        _insert_audio_intervals(job_id, audio_intervals)

        # ── Whisper STT ───────────────────────────────────────────────────────
        logger.info("[%s] Starting Whisper transcription ...", job_id)
        transcript_segments = audio_transcription.transcribe(audio_path)
        _insert_transcript(job_id, transcript_segments)

        # ── 완료 ─────────────────────────────────────────────────────────────
        _db.execute(
            "UPDATE job_history SET step2b_done=TRUE WHERE job_id=%s",
            (job_id,),
        )
        mq.publish(config.QUEUE_STEP2_GATE, {"job_id": job_id})
        logger.info("[%s] Step-2B complete → step2b_done=TRUE, published to gate", job_id)

    except Exception as exc:
        _update_job_status(job_id, "failed", str(exc))
        logger.exception("[%s] Step-2B failed: %s", job_id, exc)
        raise


# ─── Consumer entry-point ─────────────────────────────────────────────────────

def _on_message(payload: dict) -> None:
    run(payload["job_id"])


if __name__ == "__main__":
    mq.consume(config.QUEUE_STEP2B, _on_message, ack_early=True)
