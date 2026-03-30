
"""
Step 2-A — Audio Analysis + Scene Segmentation
────────────────────────────────────────────────
v2.15: 음성 우선 분석 알고리즘
  - faster-whisper large-v3 (VAD 포함): 고정밀 한국어 STT
  - ko-sroberta-multitask SBERT: 한국어 전용 의미 기반 씬 분절
  - 분절 기준: 침묵 ≥ 4.0s OR 코사인 유사도 < 0.3
  - analysis_transcript + analysis_scene (타임스탬프) DB INSERT
  - analysis_audio (침묵 구간) DB INSERT
  - 완료 시 QUEUE_STEP2B 발행 → 비전 분석(2-B) 트리거

Consumes from QUEUE_STEP2A.
"""

import logging
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import config, db as _db, rabbitmq as mq
from common.logging_setup import setup_logging
from step2_analysis import audio_analysis

setup_logging("step2a")
logger = logging.getLogger(__name__)

_SBERT_MODEL_NAME  = getattr(config, "SBERT_MODEL",            "jhgan/ko-sroberta-multitask")
_WHISPER_MODEL     = getattr(config, "FASTER_WHISPER_MODEL",   "large-v3")
_SILENCE_GAP_SEC   = float(getattr(config, "SBERT_SILENCE_GAP_SEC", "4.0"))
_SIM_THRESHOLD     = float(getattr(config, "SBERT_SIM_THRESHOLD",   "0.3"))

_sbert_model = None


# ─── 모델 로드 ────────────────────────────────────────────────────────────────

def _get_sbert_model():
    global _sbert_model
    if _sbert_model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading SBERT model: %s", _SBERT_MODEL_NAME)
        _sbert_model = SentenceTransformer(_SBERT_MODEL_NAME)
        logger.info("SBERT model loaded.")
    return _sbert_model


# ─── STT ─────────────────────────────────────────────────────────────────────

def _transcribe(audio_path: str) -> list[dict]:
    """faster-whisper large-v3 + VAD 필터 한국어 STT."""
    from faster_whisper import WhisperModel
    logger.info("[STT] Loading faster-whisper model: %s", _WHISPER_MODEL)
    model = WhisperModel(_WHISPER_MODEL, device="cpu", compute_type="int8")
    segments_gen, _ = model.transcribe(
        audio_path,
        beam_size=5,
        language="ko",
        vad_filter=True,
    )
    raw: list[dict] = []
    for seg in segments_gen:
        text = seg.text.strip()
        if text:
            raw.append({
                "start_sec": round(float(seg.start), 3),
                "end_sec":   round(float(seg.end),   3),
                "text":      text,
            })
    logger.info("[STT] %d segment(s) transcribed.", len(raw))
    return raw


# ─── SBERT 씬 분절 ────────────────────────────────────────────────────────────

def _segment_by_sbert(
    raw_segments: list[dict],
    total_duration_sec: float,
) -> list[dict]:
    """
    ko-sroberta-multitask SBERT 기반 의미 씬 분절.

    분절 기준 (whisper+bert+영상분할.py 알고리즘):
      1. 인접 문장 간 침묵 ≥ SBERT_SILENCE_GAP_SEC(4.0s) → 강제 분리
      2. 인접 문장 간 코사인 유사도 < SBERT_SIM_THRESHOLD(0.3) → 분리

    Returns:
        [{"scene_start_sec": float, "scene_end_sec": float, "transcript": str}, ...]
    """
    if not raw_segments:
        logger.info("[SBERT] No transcript — single scene fallback.")
        return [{"scene_start_sec": 0.0, "scene_end_sec": total_duration_sec, "transcript": ""}]

    try:
        from sklearn.metrics.pairwise import cosine_similarity
        model = _get_sbert_model()
    except Exception as exc:
        logger.warning("[SBERT] Unavailable (%s) — single scene fallback.", exc)
        return [{
            "scene_start_sec": 0.0,
            "scene_end_sec":   total_duration_sec,
            "transcript":      " ".join(s["text"] for s in raw_segments),
        }]

    # 씬 그룹화
    contexts: list[list[dict]] = []
    current = [raw_segments[0]]

    for i in range(1, len(raw_segments)):
        prev = raw_segments[i - 1]
        curr = raw_segments[i]

        # 침묵 기준 강제 분리
        if curr["start_sec"] - prev["end_sec"] > _SILENCE_GAP_SEC:
            contexts.append(current)
            current = [curr]
            continue

        # SBERT 유사도 분리
        emb1 = model.encode([prev["text"]])
        emb2 = model.encode([curr["text"]])
        sim  = float(cosine_similarity(emb1, emb2)[0][0])

        if sim < _SIM_THRESHOLD:
            contexts.append(current)
            current = [curr]
        else:
            current.append(curr)

    contexts.append(current)

    # 씬 목록 빌드
    scenes: list[dict] = []
    for idx, ctx in enumerate(contexts):
        s_start = ctx[0]["start_sec"]
        s_end   = ctx[-1]["end_sec"]
        # 다음 씬 시작 직전까지 씬 끝 확장 (갭 없이 연속)
        if idx + 1 < len(contexts):
            s_end = max(s_end, contexts[idx + 1][0]["start_sec"])
        else:
            s_end = max(s_end, total_duration_sec)
        scenes.append({
            "scene_start_sec": round(s_start, 3),
            "scene_end_sec":   round(s_end,   3),
            "transcript":      " ".join(s["text"] for s in ctx),
        })

    # 첫 씬 시작은 반드시 0.0
    if scenes:
        scenes[0]["scene_start_sec"] = 0.0

    logger.info(
        "[SBERT] %d segment(s) → %d scene(s) (gap=%.1fs, threshold=%.2f)",
        len(raw_segments), len(scenes), _SILENCE_GAP_SEC, _SIM_THRESHOLD,
    )
    return scenes


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _update_job_status(job_id: str, status: str, error: str | None = None) -> None:
    _db.execute(
        "UPDATE job_history SET status=%s, error_message=%s, updated_at=NOW() WHERE job_id=%s",
        (status, error, job_id),
    )


def _insert_audio_intervals(job_id: str, intervals: list[dict]) -> None:
    if not intervals:
        return
    params = [(job_id, iv["silence_start_sec"], iv["silence_end_sec"]) for iv in intervals]
    with _db.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO analysis_audio (job_id, silence_start_sec, silence_end_sec)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            params,
        )
    logger.info("Inserted %d silence interval(s).", len(intervals))


def _insert_transcript(job_id: str, segments: list[dict]) -> None:
    if not segments:
        return
    params = [(job_id, s["start_sec"], s["end_sec"], s["text"]) for s in segments]
    with _db.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO analysis_transcript (job_id, start_sec, end_sec, text)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            params,
        )
    logger.info("Inserted %d transcript segment(s).", len(segments))


def _insert_scenes(job_id: str, scenes: list[dict]) -> None:
    if not scenes:
        return
    with _db.cursor() as cur:
        for scene in scenes:
            cur.execute(
                """
                INSERT INTO analysis_scene
                    (job_id, scene_start_sec, scene_end_sec, context_narrative)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (job_id, scene_start_sec) DO UPDATE
                    SET scene_end_sec     = EXCLUDED.scene_end_sec,
                        context_narrative = EXCLUDED.context_narrative
                """,
                (
                    job_id,
                    scene["scene_start_sec"],
                    scene["scene_end_sec"],
                    scene.get("transcript", ""),
                ),
            )
    logger.info("Inserted %d scene(s).", len(scenes))


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
            "[%s] Scene segmentation already exists — redelivered, publishing to Step-2B.",
            job_id,
        )
        mq.publish(config.QUEUE_STEP2B, {"job_id": job_id})
        return

    _update_job_status(job_id, "analysing")
    try:
        info = _db.fetchone(
            "SELECT * FROM video_preprocessing_info WHERE job_id = %s",
            (job_id,),
        )
        if info is None:
            raise ValueError(f"No preprocessing info for job_id={job_id}")

        audio_path         = info["audio_path"]
        total_duration_sec = float(info["duration_sec"])

        # 1. 침묵 감지
        logger.info("[%s] Detecting silence ...", job_id)
        intervals = audio_analysis.detect_silence(audio_path)
        _insert_audio_intervals(job_id, intervals)

        # 2. faster-whisper STT
        logger.info("[%s] Starting faster-whisper STT ...", job_id)
        raw_segments = _transcribe(audio_path)
        _insert_transcript(job_id, raw_segments)

        # 3. SBERT 씬 분절
        logger.info("[%s] SBERT scene segmentation ...", job_id)
        scenes = _segment_by_sbert(raw_segments, total_duration_sec)
        _insert_scenes(job_id, scenes)

        # 4. 완료 → 2B 발행
        mq.publish(config.QUEUE_STEP2B, {"job_id": job_id})
        logger.info(
            "[%s] Step-2A complete: %d silence / %d transcript / %d scene(s) → %s",
            job_id, len(intervals), len(raw_segments), len(scenes), config.QUEUE_STEP2B,
        )

    except Exception as exc:
        _update_job_status(job_id, "failed", str(exc))
        logger.exception("[%s] Step-2A failed: %s", job_id, exc)
        raise


# ─── Consumer entry-point ─────────────────────────────────────────────────────

def _on_message(payload: dict) -> None:
    run(payload["job_id"])


if __name__ == "__main__":
    mq.consume(config.QUEUE_STEP2A, _on_message, ack_early=True)
