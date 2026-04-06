"""
Step 4 — 사전 필터
──────────────────
Cross-Encoder 전에 빠르게 후보를 제거.

  1차: 씬 길이 < 광고 길이 → Skip (유사도 계산 불필요)
  2차: 코사인 유사도 < NARRATIVE_THRESHOLD → Skip (MiniLM 빠른 임베딩)
"""

import logging
from step4_decision import embedding_scorer

logger = logging.getLogger(__name__)

NARRATIVE_THRESHOLD = 0.40  # MiniLM pre-filter 기본값


def _extract_desire(narrative: str) -> str:
    """
    narrative 텍스트에서 '욕구:' 줄만 추출한다.
    욕구 줄이 없으면 원본 전체를 반환한다.
    """
    for line in narrative.splitlines():
        if line.strip().startswith("욕구:"):
            return line.strip()
    return narrative

# 개선 3: 씬 유형별 임계값 차등 적용
# - 객체 감지 있음: 0.38 (구체적 장면, 약간 완화)
# - 긴 씬 (≥ 60초): 0.35 (롱씬은 다양한 광고 수용)
# - 짧은 씬 (< 5초): 0.45 (짧은 씬은 정밀 매칭만)
# - 기본: 0.40
_THRESHOLD_HAS_OBJECTS  = 0.38
_THRESHOLD_LONG_SCENE   = 0.35
_THRESHOLD_SHORT_SCENE  = 0.45


def get_threshold(candidate: dict) -> float:
    """씬 유형에 따라 MiniLM 임계값을 결정한다. decision.py에서도 재사용."""
    scene_duration    = float(candidate.get("scene_duration", 0))
    detected_objects  = (candidate.get("detected_objects") or "").strip()

    if scene_duration < 5.0:
        return _THRESHOLD_SHORT_SCENE
    if scene_duration >= 60.0:
        return _THRESHOLD_LONG_SCENE
    if detected_objects and detected_objects.lower() not in ("none", ""):
        return _THRESHOLD_HAS_OBJECTS
    return NARRATIVE_THRESHOLD


def passes(candidate: dict, precomputed_similarity: float | None = None) -> tuple[bool, float]:
    """
    사전 필터 적용.

    Returns:
        (passed: bool, similarity: float)
        필터 미달 시 passed=False, similarity는 계산된 값(또는 0.0) 반환.
    """
    context_narrative = (candidate.get("context_narrative") or "").strip()
    target_narrative  = (candidate.get("target_narrative") or "").strip()
    scene_duration    = float(candidate["scene_duration"])
    ad_dur            = candidate.get("ad_duration_sec")
    ad_type           = candidate.get("ad_type", "banner")
    ad_id             = candidate.get("ad_id")

    # ── 1차 필터: 씬 길이 < 광고 길이 ────────────────────────────────────────
    if ad_type == "video_clip" and ad_dur is not None:
        if scene_duration < ad_dur:
            return False, 0.0

    # ── 2차 필터: 코사인 유사도 임계치 (씬 유형별 차등) ──────────────────────
    if precomputed_similarity is not None:
        similarity = precomputed_similarity
    elif embedding_scorer.is_available() and context_narrative and target_narrative:
        # 2단계 개선: 욕구 필드만 추출하여 1:1 비교 (상황/감정 노이즈 제거)
        ctx_desire = _extract_desire(context_narrative)
        tgt_desire = _extract_desire(target_narrative)
        similarity = embedding_scorer.score_narrative_fit(ctx_desire, tgt_desire)
    else:
        similarity = 0.0

    threshold = get_threshold(candidate)
    logger.info(
        "[SIM] sim=%.4f  threshold=%.2f  dur=%.1f  ad=%s",
        similarity, threshold, scene_duration, ad_id,
    )

    if similarity < threshold:
        logger.info(
            "[SIM][SKIP] sim=%.4f < %.2f  ad=%s",
            similarity, threshold, ad_id,
        )
        return False, similarity

    return True, similarity
