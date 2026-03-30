"""
Step 4 — Cross-Encoder 스코어러
────────────────────────────────
Fine-tuning된 Cross-Encoder 모델로 (씬, 광고) 쌍의 관련도를 평가.
embedding_scorer.py(MiniLM 코사인 유사도)를 대체하는 정밀 평가 단계.

모델 경로: /app/storage/models/cross_encoder (train_cross_encoder.py 출력)
모델 없을 시: embedding_scorer로 fallback.
"""

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = "/app/storage/models/cross_encoder"

_model = None
_model_dir: str = DEFAULT_MODEL_DIR


def set_model_dir(path: str) -> None:
    """모델 경로 변경 (테스트·환경별 오버라이드용)."""
    global _model_dir
    _model_dir = path


def _get_model():
    global _model
    if _model is not None:
        return _model

    model_path = Path(_model_dir)
    if not model_path.exists():
        logger.warning(
            "Cross-Encoder model not found at %s. "
            "Run step4_training/train_cross_encoder.py first.",
            _model_dir,
        )
        return None

    try:
        from sentence_transformers import CrossEncoder
        logger.info("Loading Cross-Encoder model from %s ...", _model_dir)
        _model = CrossEncoder(str(model_path))
        logger.info("Cross-Encoder model loaded.")
    except ImportError:
        logger.warning("sentence-transformers not installed. Cross-Encoder disabled.")
        _model = None
    except Exception as exc:
        logger.warning("Failed to load Cross-Encoder model: %s", exc)
        _model = None

    return _model


def is_available() -> bool:
    return _get_model() is not None


def score(context_narrative: str, target_narrative: str) -> float:
    """
    (씬 서술문, 광고 서술문) 쌍의 관련도 점수 반환 (0.0~1.0).
    모델 미사용 시 0.0 반환.
    """
    model = _get_model()
    if model is None or not context_narrative or not target_narrative:
        return 0.0
    try:
        raw = model.predict([[context_narrative, target_narrative]])
        # ms-marco 계열 모델은 로짓 출력 → sigmoid로 0~1 정규화
        val = float(raw[0])
        return float(1 / (1 + np.exp(-val)))
    except Exception as exc:
        logger.warning("cross_encoder_scorer.score() failed: %s", exc)
        return 0.0


def batch_score(pairs: list[tuple[str, str]], batch_size: int = 256) -> list[float]:
    """
    여러 (씬, 광고) 쌍을 한 번에 평가. 단일 배치 추론으로 처리.

    Args:
        pairs: [(context_narrative, target_narrative), ...]
        batch_size: 추론 배치 크기 (기본값 256, 기존 기본값 32 대비 ~8배 빠름)

    Returns:
        각 쌍의 관련도 점수 리스트 (0.0~1.0).
    """
    model = _get_model()
    if model is None or not pairs:
        return [0.0] * len(pairs)
    try:
        raw_scores = model.predict([[c, t] for c, t in pairs], batch_size=batch_size)
        result = []
        for val in raw_scores:
            result.append(float(1 / (1 + np.exp(-float(val)))))
        return result
    except Exception as exc:
        logger.warning("cross_encoder_scorer.batch_score() failed: %s", exc)
        return [0.0] * len(pairs)
