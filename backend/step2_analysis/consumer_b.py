"""
Step 2-B — Vision Analysis (Scene-based YOLO + Gemini)
────────────────────────────────────────────────────────
v2.15: 비전 후속 분석
  - 2-A가 분절한 씬 타임스탬프 기반 K프레임 선택 (균등 샘플링)
  - YOLO: 씬별 선택 프레임에만 객체 감지 → detected_objects
  - Gemini: 대사 + 프레임 + 객체 → 상황/감정/욕구 (씬 단위)
  - analysis_scene UPDATE (situation / emotion / desire)
  - 완료 시 QUEUE_STEP3 발행

Consumes from QUEUE_STEP2B.
"""

import logging
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import config, db as _db, rabbitmq as mq
from common.logging_setup import setup_logging
from step2_analysis import vision_yolo as _yolo

# ── VLM 백엔드 선택 ──────────────────────────────────────────────────────────
_VLM_BACKEND = getattr(config, "VLM_BACKEND", "qwen").lower()
if _VLM_BACKEND == "gemini":
    from step2_analysis import vision_gemini as _vlm  # type: ignore
else:
    from step2_analysis import vision_qwen as _vlm    # type: ignore

setup_logging("step2b")
logger = logging.getLogger(__name__)

_FRAMES_PER_SCENE = int(getattr(config, "SCENE_SAMPLE_FRAMES", "3"))


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
            r.get("detected_objects", "") or None,
        )
        for r in rows
    ]
    with _db.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO analysis_vision_context
                (job_id, frame_index, timestamp_sec,
                 safe_area_x, safe_area_y, safe_area_w, safe_area_h,
                 object_density, is_scene_cut, detected_objects)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING
            """,
            params,
        )


def _update_scene_context(
    job_id: str,
    scene_start_sec: float,
    situation: str,
    emotion: str,
    desire: str,
    narrative: str,
) -> None:
    _db.execute(
        """
        UPDATE analysis_scene
           SET situation         = %s,
               emotion           = %s,
               desire            = %s,
               context_narrative = %s
         WHERE job_id = %s
           AND scene_start_sec = %s
        """,
        (situation, emotion, desire, narrative, job_id, scene_start_sec),
    )


def _already_processed(job_id: str) -> bool:
    row = _db.fetchone(
        "SELECT COUNT(*) AS cnt FROM analysis_scene WHERE job_id=%s AND situation IS NOT NULL AND situation <> ''",
        (job_id,),
    )
    return bool(row and row.get("cnt", 0) > 0)


# ─── 프레임 샘플링 ─────────────────────────────────────────────────────────────

def _sample_frames_for_scene(
    frame_paths: list[str],
    scene_start_sec: float,
    scene_end_sec: float,
    n: int = _FRAMES_PER_SCENE,
) -> list[str]:
    """씬 구간 [start, end]에서 n개 프레임 균등 선택."""
    total = len(frame_paths)
    if total == 0:
        return []
    fps       = config.FRAME_EXTRACTION_FPS
    start_idx = max(0, int(scene_start_sec * fps))
    end_idx   = min(total - 1, int(scene_end_sec * fps))
    if start_idx > end_idx:
        return []
    count = min(n, end_idx - start_idx + 1)
    float_indices = [
        start_idx + (end_idx - start_idx) * k / max(1, count - 1)
        for k in range(count)
    ]
    indices = sorted({min(total - 1, int(round(fi))) for fi in float_indices})
    return [frame_paths[i] for i in indices]


# ─── Gemini 응답 파싱 ──────────────────────────────────────────────────────────

def _parse_scene_context(narrative: str) -> tuple[str, str, str]:
    """
    "상황: ...\n감정: ...\n욕구: ..." 형식 파싱.
    Returns (situation, emotion, desire)
    """
    situation = emotion = desire = ""
    for line in narrative.splitlines():
        line = line.strip()
        if line.startswith("상황:"):
            situation = line[3:].strip()
        elif line.startswith("감정:"):
            emotion = line[3:].strip()
        elif line.startswith("욕구:"):
            desire = line[3:].strip()
    return situation, emotion, desire


# ─── Main run function ────────────────────────────────────────────────────────

def run(job_id: str) -> None:
    if _already_processed(job_id):
        logger.warning(
            "[%s] Scene context (situation/emotion/desire) already exists — "
            "redelivered, publishing to Step-3.",
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

        # 2-A가 분절한 씬 목록 로드
        scene_rows = _db.fetchall(
            "SELECT scene_start_sec, scene_end_sec FROM analysis_scene "
            "WHERE job_id=%s ORDER BY scene_start_sec",
            (job_id,),
        )
        if not scene_rows:
            raise ValueError(f"No scene data for job_id={job_id} — run Step-2A first")

        # transcript 로드
        transcript_rows = _db.fetchall(
            "SELECT start_sec, end_sec, text FROM analysis_transcript "
            "WHERE job_id=%s ORDER BY start_sec",
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

        logger.info(
            "[%s] %d scene(s), %d frame(s) total, %d transcript segment(s)",
            job_id, len(scene_rows), len(frame_paths), len(transcript_segments),
        )

        for idx, scene_row in enumerate(scene_rows):
            s_start = float(scene_row["scene_start_sec"])
            s_end   = float(scene_row["scene_end_sec"])

            # 씬별 K프레임 선택
            selected_frames = _sample_frames_for_scene(frame_paths, s_start, s_end)

            # YOLO: 선택 프레임만 실행
            yolo_rows: list[dict] = []

            def _collect_yolo(batch: list[dict]) -> None:
                yolo_rows.extend(batch)

            if selected_frames:
                _yolo.analyse_frames(selected_frames, on_batch=_collect_yolo, interval=1)
                _insert_vision_batch(job_id, yolo_rows)

            # 탐지 객체 집계 (중복 제거)
            objects_set: set[str] = set()
            for row in yolo_rows:
                for obj in (row.get("detected_objects") or "").split(","):
                    obj = obj.strip()
                    if obj:
                        objects_set.add(obj)
            detected_objects = ", ".join(sorted(objects_set))

            # 씬 구간 transcript 텍스트
            transcript_text = " ".join(
                seg["text"]
                for seg in transcript_segments
                if s_start <= seg["start_sec"] < s_end
            ).strip()

            # Gemini → 상황/감정/욕구
            narrative = ""
            try:
                narrative = _vlm.analyse_scene_context(
                    frame_paths=selected_frames,
                    transcript_text=transcript_text,
                    scene_start_sec=s_start,
                    scene_end_sec=s_end,
                    detected_objects=detected_objects,
                )
            except Exception as exc:
                logger.warning(
                    "[%s] Scene %d Gemini failed [%.1f-%.1f]: %s",
                    job_id, idx + 1, s_start, s_end, exc,
                )

            situation, emotion, desire = _parse_scene_context(narrative)
            _update_scene_context(job_id, s_start, situation, emotion, desire, narrative)

            logger.info(
                "[%s] Scene %d/%d [%.1f-%.1f] frames=%d objects=%s | 상황=%.30s",
                job_id, idx + 1, len(scene_rows), s_start, s_end,
                len(selected_frames),
                detected_objects or "(none)",
                situation or "(empty)",
            )

        _update_job_status(job_id, "persisting")
        mq.publish(config.QUEUE_STEP3, {"job_id": job_id})
        logger.info("[%s] Step-2B complete → published to %s", job_id, config.QUEUE_STEP3)

    except Exception as exc:
        _update_job_status(job_id, "failed", str(exc))
        logger.exception("[%s] Step-2B failed: %s", job_id, exc)
        raise


# ─── Consumer entry-point ─────────────────────────────────────────────────────

def _on_message(payload: dict) -> None:
    run(payload["job_id"])


if __name__ == "__main__":
    mq.consume(config.QUEUE_STEP2B, _on_message, ack_early=True)
