"""
step4_training/train_cross_encoder.py — Cross-Encoder Fine-tuning
──────────────────────────────────────────────────────────────────
cross_encoder_labels 테이블의 'train' 라벨 데이터로
ms-marco-MiniLM-L-12-v2를 Fine-tuning하여 로컬에 저장.

실행:
    python -m step4_training.train_cross_encoder [--epochs N] [--output-dir PATH]

저장 경로 기본값: /app/storage/models/cross_encoder
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from common import db as _db
from common.logging_setup import setup_logging

setup_logging("train_cross_encoder")
logger = logging.getLogger(__name__)

BASE_MODEL  = "cross-encoder/ms-marco-MiniLM-L-12-v2"
DEFAULT_OUTPUT_DIR = "/app/storage/models/cross_encoder"


# ── DB 조회 ────────────────────────────────────────────────────────────────────

def _load_train_data() -> list[dict]:
    """label='train'인 데이터만 로드."""
    rows = _db.fetchall(
        """
        SELECT context_narrative, target_narrative, gemini_score
          FROM cross_encoder_labels
         WHERE label = 'train'
         ORDER BY id
        """
    )
    logger.info("Loaded %d training samples.", len(rows))
    return rows


# ── 학습 ──────────────────────────────────────────────────────────────────────

def run(epochs: int = 3, output_dir: str = DEFAULT_OUTPUT_DIR) -> None:
    try:
        from sentence_transformers import CrossEncoder
        from sentence_transformers.cross_encoder.evaluation import CERerankingEvaluator
        from torch.utils.data import DataLoader
        from sentence_transformers import InputExample
    except ImportError:
        logger.error("sentence-transformers가 설치되지 않았습니다. pip install sentence-transformers")
        sys.exit(1)

    rows = _load_train_data()
    if not rows:
        logger.error("학습 데이터가 없습니다. labeling_gemini.py를 먼저 실행하세요.")
        sys.exit(1)

    # InputExample: (텍스트 쌍, 점수)
    train_samples = [
        InputExample(
            texts=[r["context_narrative"], r["target_narrative"]],
            label=float(r["gemini_score"]),
        )
        for r in rows
    ]

    logger.info("Base model: %s", BASE_MODEL)
    model = CrossEncoder(BASE_MODEL, num_labels=1)

    train_dataloader = DataLoader(train_samples, shuffle=True, batch_size=16)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    logger.info("Training start — epochs=%d, samples=%d, output=%s", epochs, len(train_samples), output_path)

    model.fit(
        train_dataloader=train_dataloader,
        epochs=epochs,
        warmup_steps=max(1, len(train_dataloader) // 10),
        output_path=str(output_path),
        show_progress_bar=True,
    )

    logger.info("Fine-tuning complete. Model saved to: %s", output_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cross-Encoder Fine-tuner")
    parser.add_argument("--epochs",     type=int, default=3)
    parser.add_argument("--output-dir", type=str, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    run(epochs=args.epochs, output_dir=args.output_dir)
