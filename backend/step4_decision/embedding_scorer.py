"""
Semantic embedding scorer for ad-content matching.
─────────────────────────────────────────────────
v2.5  : score_narrative_fit() — context_narrative ↔ target_narrative 1:1 유사도
v2.8  : batch_similarity_matrix() — N×M 행렬 연산으로 배치 처리
v2.9  : score_ad_context_fit() 레거시 제거 (target_mood 컬럼 삭제)

Model: jhgan/ko-sroberta-multitask
  • 한국어 특화 sentence-transformers 모델
  • 768차원 임베딩
"""

import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

MODEL_NAME = "jhgan/ko-sroberta-multitask"

_model = None


def _get_model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading sentence-transformers model: %s ...", MODEL_NAME)
            _model = SentenceTransformer(MODEL_NAME)
            logger.info("Embedding model loaded.")
        except ImportError:
            logger.warning(
                "sentence-transformers not installed. "
                "Semantic scoring disabled — falling back to keyword matching. "
                "Install with: pip install sentence-transformers"
            )
            _model = None
    return _model


def embed(text: str) -> Optional[np.ndarray]:
    """Return a normalized embedding vector, or None if model unavailable."""
    model = _get_model()
    if model is None or not text or not text.strip():
        return None
    try:
        vec = model.encode(text, normalize_embeddings=True)
        return vec
    except Exception as exc:
        logger.warning("embed() failed: %s", exc)
        return None


def compute_similarity(text_a: str, text_b: str) -> float:
    """
    Return cosine similarity (0.0–1.0) between two texts.
    Returns 0.0 if either text is empty or model is unavailable.
    """
    model = _get_model()
    if model is None or not text_a or not text_b:
        return 0.0
    try:
        vecs = model.encode([text_a, text_b], normalize_embeddings=True)
        score = float(np.dot(vecs[0], vecs[1]))
        return max(0.0, score)   # clamp to [0, 1]
    except Exception as exc:
        logger.warning("compute_similarity() failed: %s", exc)
        return 0.0


def score_narrative_fit(
    context_narrative: str,
    ad_narrative: str,
) -> float:
    """
    VOD 씬 컨텍스트와 광고 narrative 간 1:1 코사인 유사도. (v2.5 신규 — 주 경로)

    context_narrative : analyse_scene_context()가 생성한 씬 서술문
    ad_narrative      : analyze_ad_narrative.py가 생성한 4차원 광고 서술문

    기존 앙상블(0.7×sim1 + 0.3×sim2)과 달리 단일 비교 → 변별력 향상.

    Returns:
        0.0~1.0 유사도. 모델 미사용 또는 입력 없음 시 0.0.
    """
    if not context_narrative or not ad_narrative:
        return 0.0
    return compute_similarity(context_narrative, ad_narrative)


def batch_similarity_matrix(
    context_texts: list[str],
    target_texts: list[str],
) -> np.ndarray:
    """
    Compute cosine similarity matrix between all context texts and target texts.

    Returns np.ndarray of shape (len(context_texts), len(target_texts)).
    Encodes context_texts + target_texts in a single model.encode() call,
    then computes the full matrix via matrix multiply — O(N+M) encodes
    instead of O(N×M) individual calls.
    """
    model = _get_model()
    n_ctx = len(context_texts)
    n_tgt = len(target_texts)
    if model is None or n_ctx == 0 or n_tgt == 0:
        return np.zeros((n_ctx, n_tgt))
    try:
        all_vecs = model.encode(context_texts + target_texts, normalize_embeddings=True)
        ctx_vecs = all_vecs[:n_ctx]
        tgt_vecs = all_vecs[n_ctx:]
        matrix = np.dot(ctx_vecs, tgt_vecs.T)
        return np.clip(matrix, 0.0, 1.0)
    except Exception as exc:
        logger.warning("batch_similarity_matrix() failed: %s", exc)
        return np.zeros((n_ctx, n_tgt))


def is_available() -> bool:
    """Return True if sentence-transformers model is loaded and ready."""
    return _get_model() is not None
