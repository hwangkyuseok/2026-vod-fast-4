"""
dialogue_segmenter.py — Dynamic dialogue scene boundary detection
───────────────────────────────────────────────────────────────────
Detects where the topic, mood, or context meaningfully changes in the
Whisper transcript by computing cosine similarity between consecutive
dialogue chunks (sentence-transformers, same model as embedding_scorer).

Instead of a fixed look-back window, find_context_start() returns the
start of the most recent coherent scene — giving more relevant context
for ad matching at each silence interval.

Algorithm
─────────
1. Filter transcript segments in [silence - MAX_WINDOW, silence).
2. Group consecutive segments into CHUNK_DURATION-second chunks.
3. Embed each chunk's text.
4. Walk chunks from right (most recent) to left (oldest):
     • When cosine similarity between chunk[i] and chunk[i-1] drops
       below BOUNDARY_THRESHOLD → scene boundary at chunk[i].start
     • Accept the first boundary that still provides ≥ MIN_WINDOW of
       context (avoids false positives from a single short utterance).
5. If no qualifying boundary is found, use the full MAX_WINDOW.

Graceful degradation
────────────────────
If sentence-transformers is unavailable, falls back to a fixed
MAX_WINDOW (120 s default). All callers should handle the fallback
silently — the pipeline continues, just with a fixed window.
"""

import logging
from pathlib import Path

import numpy as np
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

# ── Tunable constants ─────────────────────────────────────────────────────────
CHUNK_DURATION_SEC  = 15.0   # seconds per embedding chunk
BOUNDARY_THRESHOLD  = 0.52   # similarity below this → scene/topic boundary
MIN_WINDOW_SEC      = 30.0   # never return a window shorter than this
MAX_WINDOW_SEC      = 240.0  # never look back further than this (4 min)
FALLBACK_WINDOW_SEC = 120.0  # fixed window when sentence-transformers unavailable

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"  # shared with embedding_scorer

_model = None


def _get_model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(MODEL_NAME)
            logger.info("dialogue_segmenter: model '%s' loaded.", MODEL_NAME)
        except Exception as exc:
            logger.warning(
                "dialogue_segmenter: sentence-transformers unavailable (%s). "
                "Will use fixed %.0f-second fallback window.",
                exc, FALLBACK_WINDOW_SEC,
            )
            _model = None
    return _model


# ── Internal helpers ──────────────────────────────────────────────────────────

def _group_into_chunks(segments: list[dict], chunk_duration: float) -> list[dict]:
    """
    Aggregate transcript segments into time-based chunks of ~chunk_duration seconds.

    Returns a list of dicts with keys: start_sec, end_sec, text.
    Empty text segments are skipped; a segment only advances the chunk
    boundary when it crosses the current chunk's end time.
    """
    chunks: list[dict] = []
    if not segments:
        return chunks

    chunk_start = float(segments[0]["start_sec"])
    chunk_end   = chunk_start
    chunk_texts: list[str] = []

    for seg in segments:
        s_start = float(seg.get("start_sec", 0))
        s_end   = float(seg.get("end_sec", s_start))
        text    = str(seg.get("text", "")).strip()

        if s_start >= chunk_start + chunk_duration and chunk_texts:
            # Flush completed chunk
            chunks.append({
                "start_sec": chunk_start,
                "end_sec":   chunk_end,
                "text":      " ".join(chunk_texts),
            })
            chunk_start = s_start
            chunk_texts = []

        if text:
            chunk_texts.append(text)
        chunk_end = max(chunk_end, s_end)

    if chunk_texts:
        chunks.append({
            "start_sec": chunk_start,
            "end_sec":   chunk_end,
            "text":      " ".join(chunk_texts),
        })

    return chunks


# ── Public API ────────────────────────────────────────────────────────────────

def find_context_start(
    transcript_segments: list[dict],
    silence_start_sec: float,
    min_window_sec: float = MIN_WINDOW_SEC,
    max_window_sec: float = MAX_WINDOW_SEC,
) -> float:
    """
    Find the optimal context window start time for a silence interval.

    Detects where the dialogue meaningfully changed before the silence and
    returns the start of the most recent coherent segment. The window is
    always at least *min_window_sec* and at most *max_window_sec* long.

    Args:
        transcript_segments: All Whisper segments for the job
                             (each must have start_sec, end_sec, text keys).
        silence_start_sec:   Timestamp of the silence interval start.
        min_window_sec:      Minimum context duration to always include.
        max_window_sec:      Maximum look-back limit.

    Returns:
        Float timestamp — the start of the context window to use.
    """
    window_floor = max(0.0, silence_start_sec - max_window_sec)

    # ── Filter segments that fall within the look-back window ────────────────
    candidates = [
        seg for seg in transcript_segments
        if float(seg.get("start_sec", 0)) < silence_start_sec
        and float(seg.get("start_sec", 0)) >= window_floor
    ]

    if not candidates:
        logger.debug(
            "dialogue_segmenter: no transcript in window at %.1fs → fallback %.1fs",
            silence_start_sec, window_floor,
        )
        return window_floor

    # ── Attempt embedding-based boundary detection ───────────────────────────
    model = _get_model()
    if model is None:
        fallback = max(0.0, silence_start_sec - FALLBACK_WINDOW_SEC)
        logger.debug(
            "dialogue_segmenter: model unavailable at %.1fs → fixed fallback %.1fs",
            silence_start_sec, fallback,
        )
        return fallback

    chunks = _group_into_chunks(candidates, CHUNK_DURATION_SEC)

    if len(chunks) < 2:
        # Too few chunks to detect a boundary
        start = float(candidates[0]["start_sec"])
        logger.debug(
            "dialogue_segmenter: only %d chunk(s) at %.1fs → use %.1fs",
            len(chunks), silence_start_sec, start,
        )
        return start

    # ── Embed all chunks ─────────────────────────────────────────────────────
    try:
        texts = [c["text"] for c in chunks]
        vecs  = model.encode(texts, normalize_embeddings=True)
    except Exception as exc:
        logger.warning("dialogue_segmenter: encoding failed: %s", exc)
        return window_floor

    # ── Walk chunks right→left to find the most recent qualifying boundary ───
    # We want: most recent scene change that still gives ≥ min_window_sec context.
    #
    # similarity between chunk[i-1] and chunk[i] < threshold → boundary at chunk[i].start
    # "qualifying" means silence_start_sec - chunk[i].start >= min_window_sec.

    boundary_start = float(chunks[0]["start_sec"])  # safe default: use entire window

    for i in range(len(chunks) - 1, 0, -1):
        sim = float(np.dot(vecs[i], vecs[i - 1]))
        chunk_start = float(chunks[i]["start_sec"])

        if sim < BOUNDARY_THRESHOLD:
            logger.debug(
                "dialogue_segmenter: boundary detected between chunks %d↔%d "
                "(sim=%.3f < %.2f) at %.1fs",
                i - 1, i, sim, BOUNDARY_THRESHOLD, chunk_start,
            )
            # Is this boundary far enough from the silence to give min_window context?
            if silence_start_sec - chunk_start >= min_window_sec:
                boundary_start = chunk_start
                break          # Most recent qualifying boundary — stop here
            # else: boundary is within min_window — too close, keep looking back

    final_start = max(window_floor, boundary_start)

    logger.info(
        "dialogue_segmenter: silence @%.1fs → context [%.1fs … %.1fs]  "
        "(%.0f s window, %d chunks, threshold=%.2f)",
        silence_start_sec,
        final_start,
        silence_start_sec,
        silence_start_sec - final_start,
        len(chunks),
        BOUNDARY_THRESHOLD,
    )
    return final_start


def segment_video(
    transcript_segments: list[dict],
    total_duration_sec: float,
    min_scene_sec: float = MIN_WINDOW_SEC,
) -> list[dict]:
    """
    영상 전체를 의미 단위 씬(Scene)으로 분절한다. (v2.5 신규)

    대사 타임스탬프 기반 임베딩 유사도로 토픽 전환점을 감지하여
    [{scene_start_sec, scene_end_sec}, ...] 목록을 반환.

    Phase A (forward direction): 침묵 역추적 대신 씬 전체를 먼저 분절하고
    각 씬마다 Qwen2-VL 멀티프레임 분석을 수행한 뒤, 침묵 구간을 해당 씬에
    귀속시키는 구조의 핵심 함수.

    Args:
        transcript_segments: 전체 Whisper 세그먼트 목록
                             (start_sec, end_sec, text 키 필수).
        total_duration_sec:  영상 전체 길이 (초).
        min_scene_sec:       씬 최소 길이. 이보다 짧은 씬은 인접 씬에 병합.

    Returns:
        [{"scene_start_sec": float, "scene_end_sec": float}, ...]
        항상 0.0부터 시작하며 total_duration_sec에서 종료.

    Graceful degradation:
        - transcript 없음 → 전체를 단일 씬으로 반환
        - model unavailable → FALLBACK_WINDOW_SEC 단위 고정 씬 반환
        - encoding 실패 → 단일 씬 반환
    """
    if not transcript_segments:
        logger.info(
            "segment_video: no transcript — single scene [0, %.1f]",
            total_duration_sec,
        )
        return [{"scene_start_sec": 0.0, "scene_end_sec": total_duration_sec}]

    model = _get_model()

    if model is None:
        # 고정 윈도우 씬 목록
        scenes: list[dict] = []
        t = 0.0
        while t < total_duration_sec:
            scenes.append({
                "scene_start_sec": t,
                "scene_end_sec": min(t + FALLBACK_WINDOW_SEC, total_duration_sec),
            })
            t += FALLBACK_WINDOW_SEC
        logger.info(
            "segment_video: model unavailable — %d fixed-window scene(s)", len(scenes)
        )
        return scenes

    chunks = _group_into_chunks(transcript_segments, CHUNK_DURATION_SEC)

    if len(chunks) < 2:
        logger.info(
            "segment_video: only %d chunk(s) — single scene", len(chunks)
        )
        return [{"scene_start_sec": 0.0, "scene_end_sec": total_duration_sec}]

    # ── 임베딩 ────────────────────────────────────────────────────────────────
    try:
        texts = [c["text"] for c in chunks]
        vecs  = model.encode(texts, normalize_embeddings=True)
    except Exception as exc:
        logger.warning("segment_video: encoding failed: %s", exc)
        return [{"scene_start_sec": 0.0, "scene_end_sec": total_duration_sec}]

    # ── 경계 감지 ─────────────────────────────────────────────────────────────
    # 인접 청크 간 코사인 유사도 < BOUNDARY_THRESHOLD → 씬 경계
    boundary_starts: list[float] = []
    for i in range(1, len(chunks)):
        sim = float(np.dot(vecs[i], vecs[i - 1]))
        if sim < BOUNDARY_THRESHOLD:
            boundary_starts.append(float(chunks[i]["start_sec"]))
            logger.debug(
                "segment_video: boundary at %.1fs (sim=%.3f)",
                chunks[i]["start_sec"], sim,
            )

    # ── 씬 목록 빌드 ──────────────────────────────────────────────────────────
    scene_starts = [0.0] + boundary_starts
    raw_scenes: list[dict] = []
    for i, s in enumerate(scene_starts):
        e = scene_starts[i + 1] if i + 1 < len(scene_starts) else total_duration_sec
        raw_scenes.append({"scene_start_sec": s, "scene_end_sec": e})

    # ── 짧은 씬 병합 (min_scene_sec 미만 씬을 이전 씬에 합침) ─────────────────
    merged: list[dict] = []
    for scene in raw_scenes:
        duration = scene["scene_end_sec"] - scene["scene_start_sec"]
        if merged and duration < min_scene_sec:
            # 이전 씬 끝을 현재 씬 끝으로 확장
            merged[-1]["scene_end_sec"] = scene["scene_end_sec"]
        else:
            merged.append(dict(scene))

    if not merged:
        merged = [{"scene_start_sec": 0.0, "scene_end_sec": total_duration_sec}]

    # 마지막 씬은 반드시 total_duration_sec까지
    merged[-1]["scene_end_sec"] = total_duration_sec

    logger.info(
        "segment_video: %d boundary(ies) → %d scene(s) in %.1fs video "
        "(threshold=%.2f, chunk=%.0fs)",
        len(boundary_starts), len(merged), total_duration_sec,
        BOUNDARY_THRESHOLD, CHUNK_DURATION_SEC,
    )
    return merged


def describe_context_windows(
    transcript_segments: list[dict],
    silence_intervals: list[dict],
) -> dict[float, float]:
    """
    Compute the context window start for every silence interval at once.

    Returns {silence_start_sec: context_window_start_sec}.
    Useful for logging/debugging the detected scene structure.
    """
    return {
        float(iv["silence_start_sec"]): find_context_start(
            transcript_segments,
            float(iv["silence_start_sec"]),
        )
        for iv in silence_intervals
    }
