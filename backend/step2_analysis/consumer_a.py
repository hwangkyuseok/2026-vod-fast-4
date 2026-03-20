"""
Step 2-A — Vision Analysis (YOLO + VLM Fixed Sampling)
────────────────────────────────────────────────────────
v2.13: Step2 분리 — 비전 분석 전용 컨테이너
       - YOLOv8l 프레임별 객체 탐지 → analysis_vision_context INSERT
       - VLM 고정 간격 샘플링 → scene_description UPDATE
       - 완료 시 step2a_done=TRUE, QUEUE_STEP2_GATE 발행

Consumes from QUEUE_STEP2A.
"""

import logging
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import config, db as _db, rabbitmq as mq
from common.logging_setup import setup_logging
from step2_analysis import vision_yolo as vision_rcnn

# ── VLM 백엔드 선택 ──────────────────────────────────────────────────────────
_VLM_BACKEND = getattr(config, "VLM_BACKEND", "qwen").lower()
if _VLM_BACKEND == "gemini":
    from step2_analysis import vision_gemini as _vlm  # type: ignore
else:
    from step2_analysis import vision_qwen as _vlm    # type: ignore

setup_logging("step2a")
logger = logging.getLogger(__name__)


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _update_job_status(job_id: str, status: str, error: str | None = None) -> None:
    _db.execute(
        "UPDATE job_history SET status=%s, error_message=%s, updated_at=NOW() WHERE job_id=%s",
        (status, error, job_id),
    )


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


def _already_processed(job_id: str) -> bool:
    row = _db.fetchone(
        "SELECT COUNT(*) AS cnt FROM analysis_vision_context WHERE job_id = %s",
        (job_id,),
    )
    return bool(row and row.get("cnt", 0) > 0)


# ─── Main run function ────────────────────────────────────────────────────────

def run(job_id: str) -> None:
    if _already_processed(job_id):
        logger.warning(
            "[%s] Vision context already exists — redelivered message, skipping. "
            "Setting step2a_done=TRUE and publishing to gate.",
            job_id,
        )
        _db.execute(
            "UPDATE job_history SET step2a_done=TRUE WHERE job_id=%s",
            (job_id,),
        )
        mq.publish(config.QUEUE_STEP2_GATE, {"job_id": job_id})
        return

    # 재처리 시작 — 플래그 리셋
    _db.execute(
        "UPDATE job_history SET step2a_done=FALSE WHERE job_id=%s",
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

        frame_dir   = Path(info["frame_dir_path"])
        frame_paths = sorted(str(p) for p in frame_dir.glob("*.jpg"))

        if not frame_paths:
            raise FileNotFoundError(f"No frames found in {frame_dir}")

        total_frames = len(frame_paths)
        logger.info("[%s] Total frames: %d", job_id, total_frames)

        # ── YOLO ─────────────────────────────────────────────────────────────
        logger.info("[%s] Starting YOLOv8l analysis ...", job_id)
        frames_inserted = 0

        def _on_batch(batch: list[dict]) -> None:
            nonlocal frames_inserted
            _insert_vision_batch(job_id, batch)
            frames_inserted += len(batch)

        vision_rcnn.analyse_frames(frame_paths, on_batch=_on_batch)
        logger.info("[%s] YOLO complete — %d frames streamed to DB", job_id, frames_inserted)

        # ── VLM 고정 샘플링 ───────────────────────────────────────────────────
        logger.info(
            "[%s] Starting VLM fixed-interval sampling (backend=%s) ...",
            job_id, _VLM_BACKEND,
        )
        qwen_descriptions = _vlm.analyse_frames(frame_paths)
        _update_scene_descriptions(job_id, qwen_descriptions, total_frames)

        # ── 완료 ─────────────────────────────────────────────────────────────
        _db.execute(
            "UPDATE job_history SET step2a_done=TRUE WHERE job_id=%s",
            (job_id,),
        )
        mq.publish(config.QUEUE_STEP2_GATE, {"job_id": job_id})
        logger.info("[%s] Step-2A complete → step2a_done=TRUE, published to gate", job_id)

    except Exception as exc:
        _update_job_status(job_id, "failed", str(exc))
        logger.exception("[%s] Step-2A failed: %s", job_id, exc)
        raise


# ─── Consumer entry-point ─────────────────────────────────────────────────────

def _on_message(payload: dict) -> None:
    run(payload["job_id"])


if __name__ == "__main__":
    mq.consume(config.QUEUE_STEP2A, _on_message, ack_early=True)
