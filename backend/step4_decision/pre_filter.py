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

NARRATIVE_THRESHOLD = 0.40  # MiniLM pre-filter: Cross-Encoder 입력 후보 수 제한


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

    # ── 2차 필터: 코사인 유사도 임계치 ───────────────────────────────────────
    if precomputed_similarity is not None:
        similarity = precomputed_similarity
    elif embedding_scorer.is_available() and context_narrative and target_narrative:
        similarity = embedding_scorer.score_narrative_fit(context_narrative, target_narrative)
    else:
        similarity = 0.0

    logger.info("[SIM] sim=%.4f  ctx=%.40s  ad=%s", similarity, context_narrative, ad_id)

    if similarity < NARRATIVE_THRESHOLD:
        logger.info("[SIM][SKIP] sim=%.4f < %.2f  ad=%s", similarity, NARRATIVE_THRESHOLD, ad_id)
        return False, similarity

    return True, similarity
