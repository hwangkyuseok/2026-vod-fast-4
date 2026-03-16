"""
Step 4 — Scoring & Decision Pipeline
──────────────────────────────────────
v2.0  : 기본 키워드 스코어링
v2.2  : Semantic embedding (context_summary ↔ ad_name + target_mood 앙상블)
v2.3  : 중복 INSERT 방지 (DELETE-before-INSERT), _pick_best_and_deduplicate()
v2.5  : target_narrative 우선 1:1 semantic 매칭 (score_narrative_fit)
v2.6  : Scene-driven 전환
        - 평가 순서 역전: Context Matching 1차 필터 → 물리적 수용성 2차 → 슬라이딩 윈도우
        - SCORE_SILENCE_FITS / SCORE_AFTER_CUT 제거
        - _find_best_overlay_window(): 씬 내 1초 슬라이딩 윈도우로 최적 타임스탬프 확정
        - _get_silence_overlap(): 침묵 구간 겹침 시 가점
v2.7  : 맥락 부적합 광고 억제
        - NARRATIVE_THRESHOLD 0.30 → 0.50 (느슨한 관련성 제거)
        - SCORE_SEMANTIC_MIN_SIM 0.25 → 0.40 (낮은 유사도 점수 억제)
        - MIN_SCORE_TO_KEEP 1 → 20 (유사도+밀도 복합 조건 미달 시 광고 없음 판정)
v2.10 : 카테고리 매칭 보너스 추가
        - ad_category NULL이면 보너스 없음 (graceful degradation)
        - context_narrative ↔ ad_category 유사도 ≥ 0.35 → +10점

스코어링 공식 (v2.10):
  [1차 필터] similarity < NARRATIVE_THRESHOLD(0.50) → Skip
  [2차 필터] video_clip: scene_duration < ad_duration → Skip
  0~+80   score_narrative_fit(context_narrative, target_narrative)
  +20     최적 윈도우 내 object_density ≤ 0.3
  +15     최적 윈도우 내 침묵 구간 겹침 (가점)
  +10     ad_category 매칭 보너스 (NULL이면 미적용)
  −40     최적 윈도우 내 object_density ≥ 0.7
  [최종]  score < MIN_SCORE_TO_KEEP(20) → 광고 없음 판정

Run:
    python -m step4_decision.scoring
"""

import logging
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import config, db as _db, rabbitmq as mq
from common.logging_setup import setup_logging
from step4_decision import embedding_scorer

setup_logging("step4")
logger = logging.getLogger(__name__)

# ── 스코어링 상수 (v2.7) ──────────────────────────────────────────────────────
# v2.7: 맥락 부적합 광고 억제
#   NARRATIVE_THRESHOLD  0.30→0.50 : paraphrase-multilingual-MiniLM-L12-v2 모델에서
#                                     0.3~0.5 구간은 "느슨하게 관련" 수준으로 맥락 부적합
#   SCORE_SEMANTIC_MIN_SIM 0.25→0.40: 스케일링 기준선 상향 → 낮은 유사도 점수 억제
#   MIN_SCORE_TO_KEEP      1→20    : 유사도 낮거나 밀도 불량 시 "광고 없음" 판정
NARRATIVE_THRESHOLD    = 0.50  # 1차 필터: 이 이하 similarity → Skip (맥락 무관)
SCORE_LOW_DENSITY      = 20    # 최적 윈도우 object_density ≤ 0.3
SCORE_SILENCE_BONUS    = 15    # 최적 윈도우 내 침묵 구간 겹침 가점
SCORE_CATEGORY_BONUS   = 10    # ad_category ↔ context_narrative 유사도 ≥ 0.35 (NULL이면 미적용)
CATEGORY_SIM_THRESHOLD = 0.35  # 카테고리 보너스 적용 최소 유사도
PENALTY_HIGH_DENSITY   = -40   # 최적 윈도우 object_density ≥ 0.7

SCORE_SEMANTIC_MAX     = 80    # similarity=1.0 → +80점
SCORE_SEMANTIC_MIN_SIM = 0.40  # threshold 통과 후 스케일링 하한 (NARRATIVE_THRESHOLD와 별개)

MIN_SCORE_TO_KEEP      = 20    # 이 미만 점수 후보 제거 → 맥락 부적합 광고 배제


def _update_job_status(job_id: str, status: str, error: str | None = None) -> None:
    _db.execute(
        "UPDATE job_history SET status=%s, error_message=%s, updated_at=NOW() WHERE job_id=%s",
        (status, error, job_id),
    )


def _get_scene_frames(job_id: str, scene_start: float, scene_end: float) -> list[dict]:
    """씬 범위 내 모든 vision context 프레임을 반환한다."""
    return _db.fetchall(
        """
        SELECT timestamp_sec,
               safe_area_x, safe_area_y, safe_area_w, safe_area_h,
               object_density
          FROM analysis_vision_context
         WHERE job_id = %s
           AND timestamp_sec >= %s
           AND timestamp_sec <= %s
         ORDER BY timestamp_sec
        """,
        (job_id, scene_start, scene_end),
    )


def _intersect_safe_areas(frames: list[dict]) -> tuple[int, int, int, int]:
    """
    프레임 목록의 safe area 교집합 직사각형을 반환한다.
    유효한 safe area가 없으면 (0, 0, 0, 0) 반환.
    """
    valid = [
        f for f in frames
        if f.get("safe_area_w") and f.get("safe_area_h")
        and f["safe_area_w"] > 0 and f["safe_area_h"] > 0
    ]
    if not valid:
        return (0, 0, 0, 0)

    x1 = max(f["safe_area_x"] for f in valid)
    y1 = max(f["safe_area_y"] for f in valid)
    x2 = min(f["safe_area_x"] + f["safe_area_w"] for f in valid)
    y2 = min(f["safe_area_y"] + f["safe_area_h"] for f in valid)
    w = max(0, x2 - x1)
    h = max(0, y2 - y1)
    return (x1, y1, w, h)


def _find_best_overlay_window(
    job_id: str,
    scene_start: float,
    scene_end: float,
    window_duration: float,
) -> dict | None:
    """
    씬 내에서 1초 단위 슬라이딩 윈도우로 최적 광고 삽입 구간을 탐색한다.

    최적 기준:
      1순위: safe_area 교집합 픽셀 최대
      2순위: 평균 object_density 최소

    Returns:
        {start_sec, avg_density, safe_area_px, safe_x, safe_y, safe_w, safe_h}
        또는 프레임 데이터 없을 시 None.
    """
    frames = _get_scene_frames(job_id, scene_start, scene_end)
    if not frames:
        return None

    best: dict | None = None
    t = scene_start

    while t + window_duration <= scene_end + 0.5:   # 0.5초 여유 허용
        window_end = t + window_duration
        window_frames = [f for f in frames if t <= f["timestamp_sec"] <= window_end]

        if window_frames:
            avg_density = sum(f["object_density"] or 0.0 for f in window_frames) / len(window_frames)
            sx, sy, sw, sh = _intersect_safe_areas(window_frames)
            safe_area_px = sw * sh
        else:
            avg_density  = 1.0
            sx = sy = sw = sh = 0
            safe_area_px = 0

        is_better = (
            best is None
            or safe_area_px > best["safe_area_px"]
            or (safe_area_px == best["safe_area_px"] and avg_density < best["avg_density"])
        )
        if is_better:
            best = {
                "start_sec":    t,
                "avg_density":  avg_density,
                "safe_area_px": safe_area_px,
                "safe_x": sx, "safe_y": sy, "safe_w": sw, "safe_h": sh,
            }

        t += 1.0

    return best


def _get_silence_overlap(job_id: str, window_start: float, window_end: float) -> bool:
    """최적 윈도우 구간과 겹치는 침묵 구간이 존재하면 True 반환."""
    row = _db.fetchone(
        """
        SELECT 1
          FROM analysis_audio
         WHERE job_id = %s
           AND silence_start_sec < %s
           AND silence_end_sec   > %s
         LIMIT 1
        """,
        (job_id, window_end, window_start),
    )
    return row is not None


def _compute_score(
    candidate: dict,
    job_id: str,
    precomputed_similarity: float | None = None,
) -> tuple[int, dict | None]:
    """
    v2.6 Scene-driven 스코어링.

    Returns:
        (score, window) — window에 overlay 타임스탬프와 safe area 포함.
        1차·2차 필터 미달 시 (0, None) 반환.
    """
    context_narrative = (candidate.get("context_narrative") or "").strip()
    target_narrative  = (candidate.get("target_narrative") or "").strip()
    scene_start       = float(candidate["scene_start_sec"])
    scene_end         = float(candidate["scene_end_sec"])
    scene_duration    = float(candidate["scene_duration"])
    ad_dur            = candidate.get("ad_duration_sec")
    ad_type           = candidate.get("ad_type", "banner")

    # ── 1차 필터: Narrative 유사도 임계치 ─────────────────────────────────────
    if precomputed_similarity is not None:
        similarity = precomputed_similarity
        logger.debug("narrative_fit (precomputed): sim=%.3f  ad=%s", similarity, candidate.get("ad_id"))
    elif embedding_scorer.is_available() and context_narrative and target_narrative:
        similarity = embedding_scorer.score_narrative_fit(context_narrative, target_narrative)
        logger.debug("narrative_fit: sim=%.3f  ad=%s", similarity, candidate.get("ad_id"))
    else:
        similarity = 0.0

    if similarity < NARRATIVE_THRESHOLD:
        return 0, None, similarity  # 1차 필터 미달 → Skip

    # ── 2차 필터: 물리적 수용 가능성 ─────────────────────────────────────────
    if ad_type == "video_clip" and ad_dur is not None:
        if scene_duration < ad_dur:
            return 0, None, similarity  # 씬이 광고보다 짧음 → Skip
        window_duration = ad_dur
    else:
        # 배너: 씬 내 기본 표시 시간만큼 윈도우 탐색
        window_duration = min(
            ad_dur if ad_dur is not None else config.AD_BANNER_DURATION_SEC,
            scene_duration,
        )

    # ── 3차: 슬라이딩 윈도우로 최적 타임스탬프 확정 ──────────────────────────
    window = _find_best_overlay_window(job_id, scene_start, scene_end, window_duration)
    if window is None:
        # vision 데이터 없음 → scene_start를 기본값으로 사용
        window = {
            "start_sec":    scene_start,
            "avg_density":  0.5,
            "safe_area_px": 0,
            "safe_x": None, "safe_y": None, "safe_w": None, "safe_h": None,
        }

    # ── 점수 산출 ─────────────────────────────────────────────────────────────
    score = 0

    # semantic 점수 (0~+80): NARRATIVE_THRESHOLD 통과 이후 SCORE_SEMANTIC_MIN_SIM 기준 스케일
    if similarity >= SCORE_SEMANTIC_MIN_SIM:
        scaled = (similarity - SCORE_SEMANTIC_MIN_SIM) / (1.0 - SCORE_SEMANTIC_MIN_SIM)
        score += int(scaled * SCORE_SEMANTIC_MAX)

    avg_density = window["avg_density"]

    # +20: 최적 윈도우 내 밀도 낮음
    if avg_density <= 0.3:
        score += SCORE_LOW_DENSITY

    # -40: 최적 윈도우 내 밀도 높음
    if avg_density >= 0.7:
        score += PENALTY_HIGH_DENSITY

    # +15: 최적 윈도우 내 침묵 구간 겹침 (가점만 — 필수 조건 아님)
    w_start = window["start_sec"]
    w_end   = w_start + window_duration
    if _get_silence_overlap(job_id, w_start, w_end):
        score += SCORE_SILENCE_BONUS

    # +10: 카테고리 매칭 보너스 (ad_category NULL이면 graceful skip)
    ad_category = (candidate.get("ad_category") or "").strip()
    if ad_category and context_narrative and embedding_scorer.is_available():
        cat_sim = embedding_scorer.compute_similarity(context_narrative, ad_category)
        if cat_sim >= CATEGORY_SIM_THRESHOLD:
            score += SCORE_CATEGORY_BONUS
            logger.debug(
                "category_fit: sim=%.3f → +%d  ad=%s  category=%s",
                cat_sim, SCORE_CATEGORY_BONUS, candidate.get("ad_id"), ad_category,
            )

    # similarity 반환 추가 — 레이블 데이터 수집용 피처로 decision_result에 저장
    return score, window, similarity


def _pick_best_and_deduplicate(scored: list[dict]) -> list[dict]:
    """
    1. Per unique scene_start_sec: keep only the highest-scoring ad.
    2. Sort by overlay_start_time_sec, then remove time-overlapping windows
       (greedy: keep the higher-scoring one when two overlap).
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
    result: list[dict] = []
    for c in candidates:
        start = c["overlay_start_time_sec"]
        end   = start + c["overlay_duration_sec"]
        if not result:
            result.append(c)
            continue
        prev     = result[-1]
        prev_end = prev["overlay_start_time_sec"] + prev["overlay_duration_sec"]
        if start >= prev_end:
            result.append(c)
        elif c["score"] > prev["score"]:
            result[-1] = c

    return result


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


def run(job_id: str, candidates: list[dict]) -> None:
    _update_job_status(job_id, "deciding")
    try:
        # ── 배치 행렬 연산으로 narrative 유사도 사전 계산 (v2.8) ──────────────
        # target_narrative가 있는 후보에 한해 N×M을 단일 행렬 곱으로 처리.
        # legacy(ad_name+mood) 후보는 _compute_score 내에서 개별 처리됨.
        sim_lookup: dict[tuple[str, str], float] = {}
        if embedding_scorer.is_available() and candidates:
            pairs = [
                (c.get("context_narrative") or "", c.get("target_narrative") or "")
                for c in candidates
                if c.get("context_narrative") and c.get("target_narrative")
            ]
            if pairs:
                unique_ctx = list(dict.fromkeys(p[0] for p in pairs))
                unique_tgt = list(dict.fromkeys(p[1] for p in pairs))
                sim_matrix = embedding_scorer.batch_similarity_matrix(unique_ctx, unique_tgt)
                ctx_idx = {t: i for i, t in enumerate(unique_ctx)}
                tgt_idx = {t: i for i, t in enumerate(unique_tgt)}
                for ctx, tgt in pairs:
                    if (ctx, tgt) not in sim_lookup:
                        sim_lookup[(ctx, tgt)] = float(sim_matrix[ctx_idx[ctx], tgt_idx[tgt]])
                logger.info(
                    "[%s] Batch similarity: %d pair(s) (%d ctx × %d ads) pre-computed.",
                    job_id, len(sim_lookup), len(unique_ctx), len(unique_tgt),
                )

        scored_candidates = []

        for c in candidates:
            ctx = c.get("context_narrative") or ""
            tgt = c.get("target_narrative") or ""
            precomputed = sim_lookup.get((ctx, tgt))
            score, window, similarity = _compute_score(c, job_id, precomputed_similarity=precomputed)

            if score <= 0 or window is None:
                continue  # 필터 미달 또는 점수 없음

            ad_dur  = c.get("ad_duration_sec") or config.AD_BANNER_DURATION_SEC
            ad_type = c.get("ad_type", "banner")

            if ad_type == "video_clip":
                overlay_dur = ad_dur
            else:
                overlay_dur = min(ad_dur, c["scene_duration"])

            scored_candidates.append({
                **c,
                "score":                  score,
                "similarity_score":       float(similarity),          # 레이블 피처
                "scene_duration_sec":     float(c["scene_duration"]), # 레이블 피처
                "avg_density":            float(window["avg_density"]),# 레이블 피처
                "overlay_start_time_sec": float(window["start_sec"]),
                "overlay_duration_sec":   float(overlay_dur),
                "coordinates_x":          window.get("safe_x"),
                "coordinates_y":          window.get("safe_y"),
                "coordinates_w":          window.get("safe_w"),
                "coordinates_h":          window.get("safe_h"),
            })

        best = _pick_best_and_deduplicate(scored_candidates)
        _insert_decision_results(job_id, best)
        _update_job_status(job_id, "complete")
        logger.info("[%s] Step-4 complete — %d overlays decided.", job_id, len(best))

    except Exception as exc:
        _update_job_status(job_id, "failed", str(exc))
        logger.exception("[%s] Step-4 failed: %s", job_id, exc)
        raise


def _on_message(payload: dict) -> None:
    run(payload["job_id"], payload.get("candidates", []))


if __name__ == "__main__":
    mq.consume(config.QUEUE_STEP4, _on_message)
