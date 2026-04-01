"""
Step 4 — 최종 결정 (decision.py)
──────────────────────────────────
병합 버전: GitHub v2.14 + scoring.py v3.1

GitHub v2.14 반영:
  - MiniLM pre-filter → Cross-Encoder 2단계 파이프라인
  - DB prefetch + bisect 이진탐색 (O(1) DB 왕복)
  - 모듈 분리 (pre_filter.py, cross_encoder_scorer.py)
  - 시간 겹침만 제거 (30초 간격 강제 없음)

scoring.py v3.1 반영:
  - _score_candidate() 통합 스코어링 (윈도우+점수+코너 일체)
  - Brand Safety (UNSAFE_KEYWORDS)
  - 연속 density 점수 (-40 ~ +25)
  - density trend (slope +3/-5)
  - 침묵 보너스 8점 (transcript + audio 2중 확인)
  - 4-코너 동등비교 배치 (TR→TL→BL→BR)

스코어링 공식 (병합):
  [0차 필터] Brand Safety — UNSAFE_KEYWORDS 포함 시 Skip
  [1차 필터] pre_filter.passes() — 씬길이 + MiniLM 코사인유사도 (0.40)
  [2차 필터] video_clip: scene_duration < ad_duration → Skip
  ── 슬라이딩 윈도우 + 스코어링 통합 루프 ──
    매 윈도우 위치(1초 단위)마다:
      코너 겹침 = 0 → skip (이 윈도우만)
      0~+80   semantic score (Cross-Encoder 또는 MiniLM fallback)
      -40~+25 density 연속 점수 (선형)
      +3/-5   density trend (하락 보너스는 density≤0.5일 때만)
      +8      침묵 구간 겹침 (transcript+audio 2중 확인)
      +10     ad_category 매칭 보너스
      → total_score 계산, 최고점 윈도우 선택
  ── 코너 배치 ──
    프레임별 코너 겹침으로 4-코너 동등 비교 → 겹침 면적 최대 코너에 배치
  [최종]  score < MIN_SCORE_TO_KEEP(20) → 광고 없음 판정

Run:
    python -m step4_decision.decision
"""

import bisect
import logging
from pathlib import Path

import numpy
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import config, db as _db, rabbitmq as mq
from common.logging_setup import setup_logging
from step4_decision import embedding_scorer, pre_filter, cross_encoder_scorer

setup_logging("step4")
logger = logging.getLogger(__name__)

# ── 스코어링 상수 ────────────────────────────────────────────────────────────
SCORE_SILENCE_BONUS    = 8     # 침묵 보너스 (v3.1: 15→8, density 차이 뒤집지 않게)
SCORE_GAP_BONUS        = 5     # 개선 2: 발화 gap ≥ 3초 보너스
SCORE_SCENE_OPEN_BONUS = 5     # 개선 2: 씬 시작 후 2~5초 구간 보너스 (장면전환 안착)
SCORE_CATEGORY_BONUS   = 10    # ad_category ↔ context_narrative 유사도 ≥ 0.35
CATEGORY_SIM_THRESHOLD = 0.35  # 카테고리 보너스 적용 최소 유사도

SCORE_SEMANTIC_MAX     = 80    # similarity=1.0 → +80점
# SCORE_SEMANTIC_MIN_SIM: 씬 유형별 차등 적용 → pre_filter.get_threshold() 위임 (개선 3)

MIN_SCORE_TO_KEEP      = 20    # 이 미만 점수 후보 제거 → 맥락 부적합 광고 배제

# 개선 5: 오버레이 밀도 제어
# MIN_AD_INTERVAL_SEC: 개선 3 — 영상 길이 기반 동적 계산 (_pick_best_and_deduplicate 참조)
# formula: 60 * (int(duration_sec // 1800) + 1), capped at 300s
# 10min→60s, 30min→120s, 60min→180s, 90min→240s, 120min→300s
MAX_ADS_PER_HOUR       = 10    # 시간당 최대 광고 수

# ── Brand Safety (scoring.py v3.1) ───────────────────────────────────────────
UNSAFE_KEYWORDS: list[str] = [
    "폭력", "피", "사고", "충돌", "살인",
    "자살", "성적", "노출", "장례", "죽음",
]

# ── 코너 배치 상수 (scoring.py v3.1) ─────────────────────────────────────────
DEFAULT_AD_W   = 300   # IAB 표준 배너 기본 너비
DEFAULT_AD_H   = 250   # IAB 표준 배너 기본 높이
CORNER_PADDING = 20    # 화면 가장자리 패딩
VIDEO_W        = 1280  # 기준 해상도 너비
VIDEO_H        = 720   # 기준 해상도 높이
CORNER_PRIORITY = ["TR", "TL", "BL", "BR"]  # 우상단 기본 → fallback 순서


# ── 코너 배치 함수 (scoring.py v3.1) ─────────────────────────────────────────

def _define_corners(
    ad_w: int = DEFAULT_AD_W,
    ad_h: int = DEFAULT_AD_H,
    video_w: int = VIDEO_W,
    video_h: int = VIDEO_H,
) -> dict[str, tuple[int, int]]:
    """4-코너 좌표를 반환한다 (좌상단 기준)."""
    pad = CORNER_PADDING
    return {
        "TR": (video_w - ad_w - pad, pad),
        "TL": (pad, pad),
        "BL": (pad, video_h - ad_h - pad),
        "BR": (video_w - ad_w - pad, video_h - ad_h - pad),
    }


def _corner_overlap_single(
    cx: int, cy: int, ad_w: int, ad_h: int,
    sx: int, sy: int, sw: int, sh: int,
) -> int:
    """코너 직사각형과 단일 프레임 safe_area의 겹침 면적(px)."""
    ox = max(0, min(cx + ad_w, sx + sw) - max(cx, sx))
    oy = max(0, min(cy + ad_h, sy + sh) - max(cy, sy))
    return ox * oy


def _pick_corner_from_frames(
    frames: list[dict],
    ad_w: int = DEFAULT_AD_W,
    ad_h: int = DEFAULT_AD_H,
) -> tuple[str, int, int, float] | None:
    """
    프레임별 safe_area를 각 코너와 개별 비교하여 최적 코너를 선택한다.
    4코너 동등 비교 — 평균 겹침 면적이 가장 큰 코너에 배치.
    모든 코너 평균 겹침 = 0 → None (이 윈도우 배치 불가)

    Returns:
        (corner_name, x, y, avg_overlap) 또는 None
    """
    if not frames:
        return None

    corners = _define_corners(ad_w, ad_h)
    valid_frames = [
        f for f in frames
        if f.get("safe_area_w") and f.get("safe_area_h")
        and f["safe_area_w"] > 0 and f["safe_area_h"] > 0
    ]
    if not valid_frames:
        return None

    corner_avg: dict[str, float] = {}
    for name in CORNER_PRIORITY:
        cx, cy = corners[name]
        total = sum(
            _corner_overlap_single(
                cx, cy, ad_w, ad_h,
                f["safe_area_x"], f["safe_area_y"],
                f["safe_area_w"], f["safe_area_h"],
            )
            for f in valid_frames
        )
        corner_avg[name] = total / len(valid_frames)

    best_name: str | None = None
    best_avg: float = 0.0
    for name in CORNER_PRIORITY:
        if corner_avg[name] > best_avg:
            best_avg = corner_avg[name]
            best_name = name

    if best_name is None or best_avg == 0:
        return None

    cx, cy = corners[best_name]
    return best_name, cx, cy, best_avg


# ── DB 헬퍼 ──────────────────────────────────────────────────────────────────

def _update_job_status(job_id: str, status: str, error: str | None = None) -> None:
    _db.execute(
        "UPDATE job_history SET status=%s, error_message=%s, updated_at=NOW() WHERE job_id=%s",
        (status, error, job_id),
    )


# ── 캐시 기반 헬퍼 (GitHub v2.13 캐싱 + scoring.py v3.1 침묵 로직) ──────────

def _get_scene_frames_cached(
    all_frames: list[dict],
    scene_start: float,
    scene_end: float,
) -> list[dict]:
    """v2.13: prefetch된 전체 프레임 리스트에서 씬 범위 내 프레임을 이진탐색."""
    timestamps = [float(f["timestamp_sec"]) for f in all_frames]
    lo = bisect.bisect_left(timestamps, scene_start)
    hi = bisect.bisect_right(timestamps, scene_end)
    return all_frames[lo:hi]


def _check_silence_from_cache(
    cached_transcripts: list[dict],
    has_any_transcript: bool,
    cached_audio: list[dict],
    window_start: float,
    window_end: float,
) -> bool:
    """
    scoring.py v3.1의 transcript+audio 2중 침묵 판단 (DB 호출 0건).

    1차: Whisper transcript 기반 — 윈도우 내 발화 사이 2초 이상 공백이면 침묵
    2차: transcript 없지만 job 전체에는 있으면 → 침묵
    3차: analysis_audio(librosa) fallback
    """
    overlapping = [
        t for t in cached_transcripts
        if float(t["end_sec"]) > window_start and float(t["start_sec"]) < window_end
    ]
    if overlapping:
        prev = window_start
        for t in overlapping:
            gap = float(t["start_sec"]) - prev
            if gap >= 2.0:
                return True
            prev = max(prev, float(t["end_sec"]))
        if window_end - prev >= 2.0:
            return True
        return False

    if has_any_transcript:
        return True

    for a in cached_audio:
        if float(a["silence_start_sec"]) < window_end and float(a["silence_end_sec"]) > window_start:
            return True
    return False


# ── 통합 스코어링 (scoring.py v3.1 기반, GitHub DB 캐시 적용) ────────────────

def _score_candidate(
    candidate: dict,
    job_id: str,
    precomputed_similarity: float | None = None,
    frames_cache: list[dict] | None = None,
    transcript_cache: list[dict] | None = None,
    has_any_transcript: bool = False,
    silence_cache: list[dict] | None = None,
) -> tuple[int, dict | None, float]:
    """
    v3.1 통합 스코어링 + GitHub DB 캐시.

    매 윈도우 위치(1초 단위)에서 전체 점수를 합산하여,
    총점이 가장 높은 윈도우를 선택한다.
    선택된 윈도우의 safe_area에서 4-코너 동등비교로 좌표를 결정한다.

    Returns:
        (score, window, similarity) — window에 overlay 타임스탬프 + 코너 좌표 포함.
        필터 미달 시 (0, None, similarity) 반환.
    """
    context_narrative = (candidate.get("context_narrative") or "").strip()
    target_narrative  = (candidate.get("target_narrative") or "").strip()
    scene_start       = float(candidate["scene_start_sec"])
    scene_end         = float(candidate["scene_end_sec"])
    scene_duration    = float(candidate["scene_duration"])
    ad_dur            = candidate.get("ad_duration_sec")
    ad_type           = candidate.get("ad_type", "banner")

    # ── 0차 필터: Brand Safety (scoring.py v3.1) ────────────────────────────
    if context_narrative:
        for kw in UNSAFE_KEYWORDS:
            if kw in context_narrative:
                logger.info(
                    "[SAFETY][SKIP] scene=%.1f~%.1f keyword=%s",
                    scene_start, scene_end, kw,
                )
                return 0, None, 0.0

    # ── 1차 필터: pre_filter (GitHub 모듈) ──────────────────────────────────
    # precomputed_similarity가 있으면 outer pre_filter를 이미 통과한 후보 → threshold 재검사 생략
    # (CE top-3 후보는 outer pre_filter 통과 보장, inner 재검사 시 씬 유형별 threshold 차이로 오탈락)
    if precomputed_similarity is not None:
        similarity = precomputed_similarity
    else:
        passed, similarity = pre_filter.passes(candidate, None)
        if not passed:
            return 0, None, similarity

    # ── 2차 필터: 물리적 수용 가능성 (scoring.py v3.1) ──────────────────────
    if ad_type == "video_clip" and ad_dur is not None:
        if scene_duration < ad_dur:
            return 0, None, similarity
        window_duration = ad_dur
    else:
        window_duration = min(
            ad_dur if ad_dur is not None else config.AD_BANNER_DURATION_SEC,
            scene_duration,
        )

    # ── 루프 밖 고정 점수 사전 계산 ─────────────────────────────────────────
    # semantic 점수 (0~+80) — 윈도우 위치와 무관
    # 개선 3 일관성: pre_filter와 동일한 씬 유형별 threshold를 스케일링 하한으로 사용
    semantic_min_sim = pre_filter.get_threshold(candidate)
    base_semantic = 0
    if similarity >= semantic_min_sim:
        scaled = (similarity - semantic_min_sim) / (1.0 - semantic_min_sim)
        base_semantic = int(scaled * SCORE_SEMANTIC_MAX)

    # category 보너스 — 윈도우 위치와 무관
    category_bonus = 0
    ad_category = (candidate.get("ad_category") or "").strip()
    if ad_category and context_narrative and embedding_scorer.is_available():
        cat_sim = embedding_scorer.compute_similarity(context_narrative, ad_category)
        if cat_sim >= CATEGORY_SIM_THRESHOLD:
            category_bonus = SCORE_CATEGORY_BONUS

    # ── 프레임 데이터 (GitHub 캐시 활용) ────────────────────────────────────
    if frames_cache is not None:
        frames = _get_scene_frames_cached(frames_cache, scene_start, scene_end)
    else:
        frames = _db.fetchall(
            """SELECT timestamp_sec, safe_area_x, safe_area_y, safe_area_w, safe_area_h,
                      object_density
                 FROM analysis_vision_context
                WHERE job_id = %s AND timestamp_sec >= %s AND timestamp_sec <= %s
                ORDER BY timestamp_sec""",
            (job_id, scene_start, scene_end),
        )
    if not frames:
        return 0, None, similarity

    # ── 침묵 데이터 (캐시에서 씬 범위 필터) ────────────────────────────────
    scene_transcripts = [
        t for t in (transcript_cache or [])
        if float(t["end_sec"]) > scene_start and float(t["start_sec"]) < scene_end
    ]
    scene_audio = [
        a for a in (silence_cache or [])
        if float(a["silence_start_sec"]) < scene_end and float(a["silence_end_sec"]) > scene_start
    ]

    ad_w = candidate.get("width") or DEFAULT_AD_W
    ad_h = candidate.get("height") or DEFAULT_AD_H

    # ── 슬라이딩 윈도우 + 스코어링 + 코너 배치 통합 루프 (scoring.py v3.1) ──
    best_window: dict | None = None
    best_score: int = -999
    t = scene_start

    while t + window_duration <= scene_end + 0.5:
        window_end = t + window_duration
        window_frames = [f for f in frames if t <= f["timestamp_sec"] <= window_end]

        if not window_frames:
            t += 1.0
            continue

        avg_density = sum(f["object_density"] or 0.0 for f in window_frames) / len(window_frames)

        # ── 코너 배치 판단 (scoring.py v3.1) ──
        corner_result = _pick_corner_from_frames(window_frames, ad_w, ad_h)
        if corner_result is None:
            t += 1.0
            continue

        corner_name, corner_x, corner_y, corner_overlap = corner_result

        # ── 이 윈도우의 총점 계산 ──
        total = base_semantic + category_bonus

        # density 연속 점수 (-40 ~ +25)  (scoring.py v3.1)
        density_score = int(25 - 65 * avg_density)
        density_score = max(-40, min(25, density_score))
        total += density_score

        # density trend (기울기)  (scoring.py v3.1)
        if len(window_frames) >= 2:
            ts = [f["timestamp_sec"] for f in window_frames]
            ds = [f["object_density"] or 0.0 for f in window_frames]
            slope = float(numpy.polyfit(ts, ds, 1)[0])
        else:
            slope = 0.0

        if slope < -0.02 and avg_density <= 0.5:
            total += 3
        elif slope > 0.02:
            total -= 5

        # 침묵 보너스 (scoring.py v3.1: 8점, transcript+audio 2중)
        has_silence = _check_silence_from_cache(
            scene_transcripts, has_any_transcript, scene_audio, t, window_end,
        )
        if has_silence:
            total += SCORE_SILENCE_BONUS

        # 개선 2: 발화 gap ≥ 3초 보너스 (+5)
        # 침묵 구간은 아니지만 대사 밀도가 낮은 구간에도 광고 배치 적합
        overlapping_tr = [
            tr for tr in scene_transcripts
            if float(tr["end_sec"]) > t and float(tr["start_sec"]) < window_end
        ]
        if overlapping_tr:
            prev_end = t
            for tr in overlapping_tr:
                if float(tr["start_sec"]) - prev_end >= 3.0:
                    total += SCORE_GAP_BONUS
                    break
                prev_end = max(prev_end, float(tr["end_sec"]))

        # 개선 2: 씬 시작 후 2~5초 구간 보너스 (+5)
        # 장면전환 직후 시청자 집중도가 안착되는 구간
        offset_in_scene = t - scene_start
        if 2.0 <= offset_in_scene <= 5.0:
            total += SCORE_SCENE_OPEN_BONUS

        # 최고점 갱신
        if total > best_score:
            best_score = total
            best_window = {
                "start_sec":       t,
                "avg_density":     avg_density,
                "safe_area_px":    int(corner_overlap),
                "corner_name":     corner_name,
                "corner_x":        corner_x,
                "corner_y":        corner_y,
                "corner_overlap":  corner_overlap,
                "silence_overlap": has_silence,
                "trend_slope":     slope,
            }

        t += 1.0

    # 유효한 윈도우가 없으면 (모든 윈도우의 모든 코너 겹침 = 0)
    if best_window is None:
        logger.info(
            "[CORNER][SKIP] 모든 윈도우 코너 겹침=0  ad=%s  scene=%.1f~%.1f",
            candidate.get("ad_id"), scene_start, scene_end,
        )
        return 0, None, similarity

    logger.info(
        "[v3.1] ad=%s  scene=%.1f~%.1f  t=%.1f  score=%d  corner=%s  overlap=%.0f  den=%.2f  sim=%.3f",
        candidate.get("ad_id"), scene_start, scene_end,
        best_window["start_sec"], best_score, best_window["corner_name"],
        best_window["corner_overlap"], best_window["avg_density"], similarity,
    )

    return best_score, best_window, similarity


# ── Dedup (GitHub v2.14: 시간 겹침만 제거) ──────────────────────────────────

def _pick_best_and_deduplicate(scored: list[dict], duration_sec: float = 0.0) -> list[dict]:
    """
    1. Per unique scene_start_sec: keep only the highest-scoring ad.
    2. Sort by overlay_start_time_sec, then remove time-overlapping windows
       (greedy: keep the higher-scoring one when two overlap).
    3. 개선 5: 최소 광고 간격(동적) 및 시간당 최대 광고 수(MAX_ADS_PER_HOUR) 적용.
       개선 3: 최소 간격은 영상 길이 기반 동적 계산.
    """
    # Step 1: 씬별 최고점 광고 1개
    best: dict[float, dict] = {}
    for s in scored:
        key = s["scene_start_sec"]
        if key not in best or s["score"] > best[key]["score"]:
            best[key] = s

    candidates = sorted(
        (v for v in best.values() if v["score"] >= MIN_SCORE_TO_KEEP),
        key=lambda x: x["overlay_start_time_sec"],
    )

    # Step 2: 오버레이 시간 겹침 제거
    deduped: list[dict] = []
    for c in candidates:
        start = c["overlay_start_time_sec"]
        end   = start + c["overlay_duration_sec"]
        if not deduped:
            deduped.append(c)
            continue
        prev     = deduped[-1]
        prev_end = prev["overlay_start_time_sec"] + prev["overlay_duration_sec"]
        if start >= prev_end:
            deduped.append(c)
        elif c["score"] > prev["score"]:
            deduped[-1] = c

    # Step 3: 개선 5 — 최소 간격 + 시간당 최대 광고 수 제어
    # 개선 3: 영상 길이 기반 동적 최소 광고 간격
    if duration_sec > 0:
        dynamic_interval = min(300, max(60, 60 * (int(duration_sec // 1800) + 1)))
    else:
        dynamic_interval = 120  # duration 미확인 시 기본값 2분
    logger.info(
        "[INTERVAL] duration_sec=%.1f → min_ad_interval=%ds",
        duration_sec, dynamic_interval,
    )

    result: list[dict] = []
    for c in deduped:
        start = c["overlay_start_time_sec"]

        # 최소 간격 체크
        if result:
            prev_start = result[-1]["overlay_start_time_sec"]
            if start - prev_start < dynamic_interval:
                # 간격 미달 시 점수가 높은 쪽 유지
                if c["score"] > result[-1]["score"]:
                    result[-1] = c
                continue

        # 시간당 최대 광고 수 체크
        hour_bucket = int(start // 3600)
        ads_in_hour = sum(1 for r in result if int(r["overlay_start_time_sec"] // 3600) == hour_bucket)
        if ads_in_hour >= MAX_ADS_PER_HOUR:
            logger.info(
                "[DENSITY] skip ad=%.1fs — hour %d already has %d ads (max %d)",
                start, hour_bucket, ads_in_hour, MAX_ADS_PER_HOUR,
            )
            continue

        result.append(c)

    return result


# ── DB INSERT ────────────────────────────────────────────────────────────────

def _insert_decision_results(job_id: str, results: list[dict]) -> None:
    with _db.cursor() as cur:
        cur.execute("DELETE FROM decision_result WHERE job_id = %s", (job_id,))
        deleted = cur.rowcount
        if deleted:
            logger.info("Cleared %d stale decision_result row(s) for job %s", deleted, job_id)

        for r in results:
            cur.execute(
                """
                INSERT INTO decision_result
                    (job_id, ad_id,
                     overlay_start_time_sec, overlay_duration_sec,
                     coordinates_x, coordinates_y,
                     coordinates_w, coordinates_h,
                     score,
                     similarity_score, scene_duration_sec, avg_density)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    job_id,
                    r["ad_id"],
                    float(r["overlay_start_time_sec"]),
                    float(r["overlay_duration_sec"]),
                    int(r["coordinates_x"]) if r.get("coordinates_x") is not None else None,
                    int(r["coordinates_y"]) if r.get("coordinates_y") is not None else None,
                    int(r["coordinates_w"]) if (r.get("coordinates_w") is not None and r["coordinates_w"] > 0) else None,
                    int(r["coordinates_h"]) if (r.get("coordinates_h") is not None and r["coordinates_h"] > 0) else None,
                    int(r["score"]),
                    r.get("similarity_score"),    # 레이블 피처 ①
                    r.get("scene_duration_sec"),  # 레이블 피처 ②
                    r.get("avg_density"),         # 레이블 피처 ③
                ),
            )
    logger.info("Inserted %d decision result(s) for job %s", len(results), job_id)


# ── 메인 파이프라인 ──────────────────────────────────────────────────────────

def run(job_id: str, candidates: list[dict], duration_sec: float = 0.0) -> None:
    _update_job_status(job_id, "deciding")
    try:
        sim_lookup: dict[tuple[str, str], float] = {}
        ctx_to_desire: dict[str, str] = {}    # 개선 4: 씬 desire 매핑
        desire_lookup: dict[tuple[str, str], float] = {}  # 개선 4: desire × target 유사도

        # ── 1단계: MiniLM pre-filter (GitHub v2.14) ─────────────────────────
        # Cross-Encoder 전에 MiniLM 코사인유사도로 대량 후보를 빠르게 제거
        # v2.15+: 씬당 상위 EMBED_TOP_K_PER_SCENE개만 Cross-Encoder에 넘김
        EMBED_TOP_K_PER_SCENE = 30
        if candidates and embedding_scorer.is_available():
            unique_ctx = list(dict.fromkeys(
                c.get("context_narrative") or "" for c in candidates
                if c.get("context_narrative") and c.get("target_narrative")
            ))
            unique_tgt = list(dict.fromkeys(
                c.get("target_narrative") or "" for c in candidates
                if c.get("context_narrative") and c.get("target_narrative")
            ))
            minilm_lookup: dict[tuple[str, str], float] = {}
            if unique_ctx and unique_tgt:
                sim_matrix = embedding_scorer.batch_similarity_matrix(unique_ctx, unique_tgt)
                ctx_idx = {t: i for i, t in enumerate(unique_ctx)}
                tgt_idx = {t: i for i, t in enumerate(unique_tgt)}
                for c in candidates:
                    ctx = c.get("context_narrative") or ""
                    tgt = c.get("target_narrative") or ""
                    if ctx and tgt:
                        minilm_lookup[(ctx, tgt)] = float(sim_matrix[ctx_idx[ctx], tgt_idx[tgt]])

                # 개선 4: desire 임베딩 블렌딩 (씬 desire ↔ target_narrative)
                for c in candidates:
                    ctx = c.get("context_narrative") or ""
                    desire = c.get("desire") or ""
                    if ctx and desire:
                        ctx_to_desire[ctx] = desire
                unique_desire = list(dict.fromkeys(v for v in ctx_to_desire.values() if v))
                if unique_desire:
                    desire_sim_matrix = embedding_scorer.batch_similarity_matrix(unique_desire, unique_tgt)
                    desire_idx_map = {t: i for i, t in enumerate(unique_desire)}
                    for c in candidates:
                        ctx = c.get("context_narrative") or ""
                        tgt = c.get("target_narrative") or ""
                        if not (ctx and tgt):
                            continue
                        desire = ctx_to_desire.get(ctx, "")
                        if desire and desire in desire_idx_map and tgt in tgt_idx:
                            ctx_sim = minilm_lookup.get((ctx, tgt), 0.0)
                            d_sim = float(desire_sim_matrix[desire_idx_map[desire], tgt_idx[tgt]])
                            minilm_lookup[(ctx, tgt)] = 0.7 * ctx_sim + 0.3 * d_sim
                            desire_lookup[(ctx, tgt)] = d_sim
                    logger.info(
                        "[%s] Desire blending: %d desires × %d ads blended into minilm_lookup",
                        job_id, len(unique_desire), len(unique_tgt),
                    )

            before = len(candidates)

            # 임계값 통과 후보 수집
            filtered = []
            for c in candidates:
                ctx = c.get("context_narrative") or ""
                tgt = c.get("target_narrative") or ""
                precomputed = minilm_lookup.get((ctx, tgt))
                passed, _ = pre_filter.passes(c, precomputed)
                if passed:
                    filtered.append(c)

            # 씬(context_narrative)별로 유사도 상위 EMBED_TOP_K_PER_SCENE개만 유지
            from collections import defaultdict
            scene_buckets: dict[str, list[tuple[float, dict]]] = defaultdict(list)
            for c in filtered:
                ctx = c.get("context_narrative") or ""
                tgt = c.get("target_narrative") or ""
                sim = minilm_lookup.get((ctx, tgt), 0.0)
                scene_buckets[ctx].append((sim, c))

            candidates = []
            for ctx, items in scene_buckets.items():
                items.sort(key=lambda x: x[0], reverse=True)
                candidates.extend(c for _, c in items[:EMBED_TOP_K_PER_SCENE])

            logger.info(
                "[%s] pre-filter: %d → %d (threshold) → %d (top-%d/scene)",
                job_id, before, len(filtered), len(candidates), EMBED_TOP_K_PER_SCENE,
            )

        # ── 2단계: Cross-Encoder 배치 → 씬별 Top-3 (Bug 2 fix: 전역 Top-30 제거) ──
        CE_TOP_K_PER_SCENE = 3
        if candidates:
            pairs = [
                (c.get("context_narrative") or "", c.get("target_narrative") or "")
                for c in candidates
                if c.get("context_narrative") and c.get("target_narrative")
            ]
            if pairs:
                unique_pairs = list(dict.fromkeys(pairs))

                if cross_encoder_scorer.is_available():
                    # Cross-Encoder 배치 추론 (정밀 평가)
                    scores = cross_encoder_scorer.batch_score(unique_pairs)
                    sim_lookup = dict(zip(unique_pairs, scores))
                    # 개선 4: CE 단계 desire 블렌딩 비활성화
                    # (pre-filter 단계에서만 0.7/0.3 블렌딩 적용, CE 점수는 순수하게 사용)
                    logger.info(
                        "[%s] Cross-Encoder batch: %d pair(s) scored.",
                        job_id, len(sim_lookup),
                    )
                    # Bug 2 fix: 전역 Top-K 대신 씬별 Top-CE_TOP_K_PER_SCENE 선택
                    # 기존 전역 정렬 → 동일 씬 독점 → 실질 1~3씬 문제 해결
                    scored_list = [
                        (c, sim_lookup.get(
                            (c.get("context_narrative") or "", c.get("target_narrative") or ""), 0.0
                        ))
                        for c in candidates
                    ]
                    from collections import defaultdict as _dd
                    ce_buckets: dict[str, list] = _dd(list)
                    for c, sim in scored_list:
                        ce_buckets[c.get("context_narrative") or ""].append((sim, c))
                    candidates = []
                    for items in ce_buckets.values():
                        items.sort(key=lambda x: x[0], reverse=True)
                        candidates.extend(c for _, c in items[:CE_TOP_K_PER_SCENE])
                    logger.info(
                        "[%s] CE Top-%d/scene: %d씬 → %d 후보 (from %d).",
                        job_id, CE_TOP_K_PER_SCENE, len(ce_buckets),
                        len(candidates), len(scored_list),
                    )
                elif not sim_lookup:
                    # CE 없으면 MiniLM fallback
                    unique_ctx = list(dict.fromkeys(p[0] for p in unique_pairs))
                    unique_tgt = list(dict.fromkeys(p[1] for p in unique_pairs))
                    sim_matrix = embedding_scorer.batch_similarity_matrix(unique_ctx, unique_tgt)
                    ctx_idx = {t: i for i, t in enumerate(unique_ctx)}
                    tgt_idx = {t: i for i, t in enumerate(unique_tgt)}
                    for ctx, tgt in unique_pairs:
                        sim_lookup[(ctx, tgt)] = float(sim_matrix[ctx_idx[ctx], tgt_idx[tgt]])
                    logger.info(
                        "[%s] Fallback MiniLM batch: %d pair(s) pre-computed.",
                        job_id, len(sim_lookup),
                    )

        # ── DB prefetch (GitHub v2.13) ──────────────────────────────────────
        # 루프 내 씬별 DB 쿼리(O(N))를 루프 전 1회 전체 조회(O(1))로 개선
        frames_cache = _db.fetchall(
            """SELECT timestamp_sec,
                      safe_area_x, safe_area_y, safe_area_w, safe_area_h,
                      object_density
                 FROM analysis_vision_context
                WHERE job_id = %s
                ORDER BY timestamp_sec""",
            (job_id,),
        )
        silence_cache = _db.fetchall(
            "SELECT silence_start_sec, silence_end_sec FROM analysis_audio WHERE job_id = %s",
            (job_id,),
        )
        # scoring.py v3.1: transcript 캐시 추가 (2중 침묵 판단용)
        transcript_cache = _db.fetchall(
            "SELECT start_sec, end_sec FROM analysis_transcript WHERE job_id = %s ORDER BY start_sec",
            (job_id,),
        )
        has_any_transcript = len(transcript_cache) > 0
        logger.info(
            "[%s] Prefetched %d vision frames, %d silence intervals, %d transcripts.",
            job_id, len(frames_cache), len(silence_cache), len(transcript_cache),
        )

        # ── 스코어링 루프 (scoring.py v3.1 통합 방식) ───────────────────────
        scored_candidates = []

        for c in candidates:
            ctx = c.get("context_narrative") or ""
            tgt = c.get("target_narrative") or ""
            # CE 점수는 후보 순위 선별용. 실제 scoring에는 threshold를 통과한 minilm 점수 사용
            ml_val = minilm_lookup.get((ctx, tgt))
            sl_val = sim_lookup.get((ctx, tgt))
            precomputed = ml_val if ml_val is not None else sl_val
            score, window, similarity = _score_candidate(
                c, job_id,
                precomputed_similarity=precomputed,
                frames_cache=frames_cache,
                transcript_cache=transcript_cache,
                has_any_transcript=has_any_transcript,
                silence_cache=silence_cache,
            )

            if score <= 0 or window is None:
                continue

            ad_dur  = c.get("ad_duration_sec") or config.AD_BANNER_DURATION_SEC
            ad_type = c.get("ad_type", "banner")

            if ad_type == "video_clip":
                overlay_dur = ad_dur
            else:
                overlay_dur = min(ad_dur, c["scene_duration"])

            scored_candidates.append({
                **c,
                "score":                  score,
                "similarity_score":       float(similarity),
                "scene_duration_sec":     float(c["scene_duration"]),
                "avg_density":            float(window["avg_density"]),
                "overlay_start_time_sec": float(window["start_sec"]),
                "overlay_duration_sec":   float(overlay_dur),
                "coordinates_x":          window.get("corner_x", CORNER_PADDING),
                "coordinates_y":          window.get("corner_y", CORNER_PADDING),
                "coordinates_w":          c.get("width") or DEFAULT_AD_W,
                "coordinates_h":          c.get("height") or DEFAULT_AD_H,
                "corner_name":            window.get("corner_name", "TR"),
            })

        # ── Dedup + INSERT (GitHub v2.14: 시간 겹침만 제거) ─────────────────
        best = _pick_best_and_deduplicate(scored_candidates, duration_sec=duration_sec)
        _insert_decision_results(job_id, best)
        _update_job_status(job_id, "complete")
        logger.info("[%s] Step-4 complete — %d overlays decided.", job_id, len(best))

    except Exception as exc:
        _update_job_status(job_id, "failed", str(exc))
        logger.exception("[%s] Step-4 failed: %s", job_id, exc)
        raise


def _on_message(payload: dict) -> None:
    job_id = payload["job_id"]
    # v2.13: lazy import — 모듈 레벨 import 시 setup_logging("step3")이 실행되어
    # step4 log handler가 step3.log로 교체되는 문제 방지
    from step3_persistence.pipeline import build_candidates
    candidates = build_candidates(job_id)
    # 개선 3: 영상 길이 조회 → 동적 최소 광고 간격 계산
    row = _db.fetchone(
        "SELECT duration_sec FROM video_preprocessing_info WHERE job_id = %s",
        (job_id,),
    )
    duration_sec = float(row["duration_sec"]) if row and row.get("duration_sec") else 0.0
    run(job_id, candidates, duration_sec=duration_sec)


if __name__ == "__main__":
    mq.consume(config.QUEUE_STEP4, _on_message)
