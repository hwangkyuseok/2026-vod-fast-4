"""
step4_training/train_cross_encoder.py — Cross-Encoder Fine-tuning
──────────────────────────────────────────────────────────────────
cross_encoder_labels 테이블의 positive/negative 라벨 데이터로
ms-marco-MiniLM-L-12-v2를 Fine-tuning하여 로컬에 저장.

v2.0 변경사항:
  - train/test 분리를 pair 단위 랜덤 → scene_id 단위로 변경
    (동일 씬이 train/test 양쪽에 들어가는 데이터 누수 방지)
  - 평가 지표: CERerankingEvaluator (씬별 positive 랭킹 정확도)

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

def _load_train_data(neg_ratio: int = 3, test_ratio: float = 0.2) -> tuple[list[dict], list[dict]]:
    """
    positive/negative 라벨 데이터를 씬(scene_id) 단위로 train/test 분리.

    v2.0: pair 단위 랜덤 분리 → scene_id 단위 분리로 변경.
    동일 씬이 train/test 양쪽에 들어가는 데이터 누수(leakage)를 방지.

    neg_ratio  : positive 1건당 negative 최대 비율 (기본 1:3)
    test_ratio : test set 비율 (기본 0.2 = 20%)

    Returns:
        (train_rows, test_rows)
    """
    import random
    import math

    rows = _db.fetchall(
        """
        SELECT scene_id, context_narrative, target_narrative, gemini_score, label
          FROM cross_encoder_labels
         WHERE label IN ('positive', 'negative')
         ORDER BY scene_id, id
        """
    )
    if not rows:
        return [], []

    # ── 씬 단위 분리 ──────────────────────────────────────────────────────────
    from collections import defaultdict
    scene_map: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        scene_map[r["scene_id"]].append(r)

    all_scene_ids = list(scene_map.keys())
    random.shuffle(all_scene_ids)

    n_test = max(1, math.ceil(len(all_scene_ids) * test_ratio))
    test_scene_ids  = set(all_scene_ids[:n_test])
    train_scene_ids = set(all_scene_ids[n_test:])

    train_all = [r for sid in train_scene_ids for r in scene_map[sid]]
    test_all  = [r for sid in test_scene_ids  for r in scene_map[sid]]

    logger.info(
        "Scene-level split: train_scenes=%d, test_scenes=%d "
        "(train_pairs=%d, test_pairs=%d)",
        len(train_scene_ids), len(test_scene_ids),
        len(train_all), len(test_all),
    )

    # ── train: negative 다운샘플링 ────────────────────────────────────────────
    train_pos = [r for r in train_all if r["label"] == "positive"]
    train_neg = [r for r in train_all if r["label"] == "negative"]

    max_neg = len(train_pos) * neg_ratio
    if len(train_neg) > max_neg:
        train_neg = random.sample(train_neg, max_neg)

    train_rows = train_pos + train_neg
    random.shuffle(train_rows)

    logger.info(
        "Train — positive=%d, negative=%d (ratio 1:%d) / total=%d",
        len(train_pos), len(train_neg), neg_ratio, len(train_rows),
    )
    logger.info(
        "Test  — positive=%d, negative=%d / total=%d",
        sum(1 for r in test_all if r["label"] == "positive"),
        sum(1 for r in test_all if r["label"] == "negative"),
        len(test_all),
    )

    return train_rows, test_all


# ── 학습 ──────────────────────────────────────────────────────────────────────

def run(epochs: int = 3, output_dir: str = DEFAULT_OUTPUT_DIR, neg_ratio: int = 3) -> None:
    try:
        from sentence_transformers import CrossEncoder
        from sentence_transformers.cross_encoder.evaluation import CERerankingEvaluator
        from torch.utils.data import DataLoader
        from sentence_transformers import InputExample
    except ImportError:
        logger.error("sentence-transformers가 설치되지 않았습니다. pip install sentence-transformers")
        sys.exit(1)

    # v2.0: scene_id 단위 train/test 분리
    train_rows, test_rows = _load_train_data(neg_ratio=neg_ratio)
    if not train_rows:
        logger.error("학습 데이터가 없습니다. labeling_gemini.py를 먼저 실행하세요.")
        sys.exit(1)

    def to_samples(data: list[dict]) -> list:
        return [
            InputExample(
                texts=[r["context_narrative"], r["target_narrative"]],
                label=float(r["gemini_score"]),
            )
            for r in data
        ]

    train_samples = to_samples(train_rows)
    test_samples  = to_samples(test_rows)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_path / "checkpoints"

    # 체크포인트가 있으면 이어서 학습, 없으면 베이스 모델로 시작
    existing = sorted(checkpoint_path.glob("*-steps")) if checkpoint_path.exists() else []
    if existing:
        resume_from = str(existing[-1])
        logger.info("Resuming from checkpoint: %s", resume_from)
        model = CrossEncoder(resume_from, num_labels=1)
    else:
        logger.info("Base model: %s", BASE_MODEL)
        model = CrossEncoder(BASE_MODEL, num_labels=1)

    train_dataloader = DataLoader(train_samples, shuffle=True, batch_size=16)

    # 에포크마다 test set으로 평가 (씬별로 positive/negative 묶기)
    from collections import defaultdict
    scene_pos = defaultdict(list)
    scene_neg = defaultdict(list)
    for s in test_samples:
        if s.label >= 0.7:
            scene_pos[s.texts[0]].append(s.texts[1])
        else:
            scene_neg[s.texts[0]].append(s.texts[1])

    eval_samples = [
        {"query": query, "positive": pos, "negative": scene_neg.get(query, [])}
        for query, pos in scene_pos.items()
        if scene_neg.get(query)  # negative가 있는 씬만 포함
    ]
    evaluator = CERerankingEvaluator(samples=eval_samples, name="test") if eval_samples else None
    if evaluator:
        logger.info("Evaluator: %d scene queries", len(eval_samples))
    else:
        logger.warning("Evaluator 구성 불가 — test set에 positive/negative 쌍이 부족합니다.")

    logger.info("Training start — epochs=%d, train=%d, test=%d, output=%s",
                epochs, len(train_samples), len(test_samples), output_path)

    model.fit(
        train_dataloader=train_dataloader,
        evaluator=evaluator,
        epochs=epochs,
        warmup_steps=max(1, len(train_dataloader) // 10),
        output_path=str(output_path),
        show_progress_bar=True,
    )

    model.save(str(output_path))
    logger.info("Fine-tuning complete. Model saved to: %s", output_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cross-Encoder Fine-tuner")
    parser.add_argument("--epochs",     type=int, default=3)
    parser.add_argument("--output-dir", type=str, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--neg-ratio",  type=int,   default=3,   help="positive 1건당 negative 최대 비율 (기본 1:3)")
    parser.add_argument("--test-ratio", type=float, default=0.2, help="test set 비율 (기본 0.2 = 20%%)")
    args = parser.parse_args()

    run(epochs=args.epochs, output_dir=args.output_dir, neg_ratio=args.neg_ratio)
