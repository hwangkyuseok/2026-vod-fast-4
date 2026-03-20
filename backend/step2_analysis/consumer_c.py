"""
Step 2-C — Phase A: Scene Segmentation + VLM Narrative
────────────────────────────────────────────────────────
v2.13: Step2 분리 — Phase A 전용 컨테이너
       - DB 플래그 게이트: step2a_done AND step2b_done 모두 TRUE여야 실행
       - dialogue_segmenter.segment_video(): 씬 경계 탐지
       - VLM analyse_scene_context(): 씬별 멀티모달 narrative 생성
       - 완료 시 QUEUE_STEP3 발행

Consumes from QUEUE_STEP2_GATE.

게이트 동작:
  - 2-A, 2-B가 각각 QUEUE_STEP2_GATE에 job_id 발행
  - consumer_c는 메시지 수신 후 두 플래그를 폴링 (최대 30분)
  - 두 플래그 모두 TRUE가 되면 Phase A 실행
"""

import logging
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import config, db as _db, rabbitmq as mq
from common.logging_setup import setup_logging
from step2_analysis import dialogue_segmenter

# ── VLM 백엔드 선택 ──────────────────────────────────────────────────────────
_VLM_BACKEND = getattr(config, "VLM_BACKEND", "qwen").lower()
if _VLM_BACKEND == "gemini":
    from step2_analysis import vision_gemini as _vlm  # type: ignore
else:
    from step2_analysis import vision_qwen as _vlm    # type: ignore

setup_logging("step2c")
logger = logging.getLogger(__name__)

# ── 게이트 폴링 설정 ─────────────────────────────────────────────────────────
_GATE_POLL_INTERVAL_SEC = 30    # 30초마다 체크
_GATE_MAX_WAIT_SEC      = 1800  # 최대 30분 대기


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _update_job_status(job_id: str, status: str, error: str | None = None) -> None:
    _db.execute(
        "UPDATE job_history SET status=%s, error_message=%s, updated_at=NOW() WHERE job_id=%s",
        (status, error, job_id),
    )


def _wait_for_gate(job_id: str) -> bool:
    """
    step2a_done AND step2b_done 모두 TRUE가 될 때까지 폴링.
    최대 _GATE_MAX_WAIT_SEC 대기 후 False 반환.
    """
    elapsed = 0
    while elapsed < _GATE_MAX_WAIT_SEC:
        row = _db.fetchone(
            "SELECT step2a_done, step2b_done FROM job_history WHERE job_id=%s",
            (job_id,),
        )
        if row and row["step2a_done"] and row["step2b_done"]:
            return True
        logger.info(
            "[%s] Gate waiting... (a=%s, b=%s, elapsed=%ds)",
            job_id,
            row["step2a_done"] if row else "?",
            row["step2b_done"] if row else "?",
            elapsed,
        )
        time.sleep(_GATE_POLL_INTERVAL_SEC)
        elapsed += _GATE_POLL_INTERVAL_SEC
    return False


def _insert_scene_context(
    job_id: str,
    scene_start_sec: float,
    scene_end_sec: float,
    context_narrative: str,
) -> None:
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


def _assign_scene_context_to_silences(job_id: str, scenes: list[dict]) -> None:
    if not scenes:
        return
    silence_rows = _db.fetchall(
        "SELECT silence_start_sec FROM analysis_audio WHERE job_id = %s ORDER BY silence_start_sec",
        (job_id,),
    )
    updated = 0
    for row in silence_rows:
        start     = float(row["silence_start_sec"])
        narrative = ""
        for scene in scenes:
            if scene["scene_start_sec"] <= start < scene["scene_end_sec"]:
                narrative = scene.get("context_narrative") or ""
                break
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


def _sample_frames_for_scene(
    frame_paths: list[str],
    scene_start_sec: float,
    scene_end_sec: float,
    n: int = 4,
) -> list[str]:
    total = len(frame_paths)
    if total == 0:
        return []
    start_idx = max(0, int(scene_start_sec * config.FRAME_EXTRACTION_FPS))
    end_idx   = min(total - 1, int(scene_end_sec * config.FRAME_EXTRACTION_FPS))
    if start_idx > end_idx:
        return []
    if end_idx == start_idx:
        return [frame_paths[start_idx]]
    count = min(n, end_idx - start_idx + 1)
    import numpy as np
    float_indices = [
        start_idx + (end_idx - start_idx) * k / max(1, count - 1)
        for k in range(count)
    ]
    indices = sorted({min(total - 1, int(round(fi))) for fi in float_indices})
    return [frame_paths[i] for i in indices]


def _generate_scene_contexts(
    job_id: str,
    transcript_segments: list[dict],
    frame_paths: list[str],
    total_duration_sec: float,
    visual_cut_times: list[float] | None = None,
) -> None:
    logger.info("[%s] Phase A: scene segmentation + context generation ...", job_id)
    scenes = dialogue_segmenter.segment_video(
        transcript_segments=transcript_segments,
        total_duration_sec=total_duration_sec,
        visual_cut_times=visual_cut_times,
    )
    logger.info("[%s] Detected %d scene(s).", job_id, len(scenes))

    for idx, scene in enumerate(scenes):
        s_start = float(scene["scene_start_sec"])
        s_end   = float(scene["scene_end_sec"])

        transcript_text = " ".join(
            seg["text"]
            for seg in transcript_segments
            if s_start <= float(seg.get("start_sec", 0)) < s_end
        ).strip()

        sampled_frames = _sample_frames_for_scene(frame_paths, s_start, s_end, n=4)

        narrative = ""
        try:
            narrative = _vlm.analyse_scene_context(
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

        scene["context_narrative"] = narrative
        _insert_scene_context(job_id, s_start, s_end, narrative)

        logger.info(
            "[%s] Scene %d/%d [%.1f-%.1f] | frames=%d | narrative=%d chars",
            job_id, idx + 1, len(scenes), s_start, s_end,
            len(sampled_frames), len(narrative),
        )

    _assign_scene_context_to_silences(job_id, scenes)
    logger.info("[%s] Phase A complete.", job_id)


def _already_processed(job_id: str) -> bool:
    row = _db.fetchone(
        "SELECT COUNT(*) AS cnt FROM analysis_scene WHERE job_id = %s",
        (job_id,),
    )
    return bool(row and row.get("cnt", 0) > 0)


# ─── Main run function ────────────────────────────────────────────────────────

def run(job_id: str) -> None:
    if _already_processed(job_id):
        logger.warning(
            "[%s] Scene context already exists — redelivered message, skipping. "
            "Publishing to Step-3 again.",
            job_id,
        )
        mq.publish(config.QUEUE_STEP3, {"job_id": job_id})
        return

    # ── 게이트: 2-A + 2-B 모두 완료될 때까지 폴링 ──────────────────────────
    if not _wait_for_gate(job_id):
        _update_job_status(
            job_id, "failed",
            "step2c gate timeout: step2a/b did not complete within the allowed time",
        )
        logger.error("[%s] Gate timeout — Phase A aborted.", job_id)
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
        total_duration_sec = float(info["duration_sec"])

        # consumer_b가 이미 INSERT 완료한 transcript를 DB에서 조회
        transcript_rows = _db.fetchall(
            """
            SELECT start_sec, end_sec, text
              FROM analysis_transcript
             WHERE job_id = %s
             ORDER BY start_sec
            """,
            (job_id,),
        )
        transcript_segments = [
            {
                "start_sec": float(r["start_sec"]),
                "end_sec":   float(r["end_sec"]),
                "text":      r["text"],
            }
            for r in transcript_rows
        ]

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
        logger.info("[%s] Step-2C complete → published to %s", job_id, config.QUEUE_STEP3)

    except Exception as exc:
        _update_job_status(job_id, "failed", str(exc))
        logger.exception("[%s] Step-2C failed: %s", job_id, exc)
        raise


# ─── Consumer entry-point ─────────────────────────────────────────────────────

def _on_message(payload: dict) -> None:
    run(payload["job_id"])


if __name__ == "__main__":
    mq.consume(config.QUEUE_STEP2_GATE, _on_message, ack_early=True)
