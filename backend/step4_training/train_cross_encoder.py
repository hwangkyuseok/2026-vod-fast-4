"""
step4_training/train_cross_encoder.py — Cross-Encoder Fine-tuning
──────────────────────────────────────────────────────────────────
cross_encoder_labels 테이블의 positive/negative 라벨 데이터로
ms-marco-MiniLM-L-12-v2를 Fine-tuning하여 로컬에 저장.

실행:
    python -m step4_training.train_cross_encoder [--epochs N] [--output-dir PATH]

저장 경로 기본값: /app/storage/models/cross_encoder

[테이블 역할]
  cross_encoder_labels
    split      : 'train' | 'test' | NULL
                 NULL → 최초 실행 시 scene_id 단위 80/20 할당 후 영구 저장
    trained_at : 실제 학습에 사용된 시각 (NULL = 아직 미사용)

  ce_training_runs
    학습 실행 이력 (run_at, train_count, test_count, epochs, model_path)

[학습 흐름]
  1. split=NULL 행 → scene_id 단위 80/20 자동 할당 (DB 영구 저장)
  2. split='train' 행 전체 로드 → negative 다운샘플링
  3. split='test'  행 전체 로드 → 평가 전용
  4. ce_training_runs INSERT (학습 시작 기록)
  5. Fine-tuning 실행
  6. cross_encoder_labels.trained_at = NOW() 업데이트
  7. ce_training_runs 실제 건수/경로 업데이트
"""

import argparse
import logging
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from common import db as _db
from common.logging_setup import setup_logging

setup_logging("train_cross_encoder")
logger = logging.getLogger(__name__)

BASE_MODEL         = "cross-encoder/ms-marco-MiniLM-L-12-v2"
DEFAULT_OUTPUT_DIR = "/app/storage/models/cross_encoder"
TEST_RATIO         = 0.2


# ── split 할당 ─────────────────────────────────────────────────────────────────

def _assign_split_if_needed() -> None:
    """
    split=NULL 인 positive/negative 행이 있으면 scene_id 단위로 train/test 할당.
    이미 split이 지정된 행은 건드리지 않는다.
    """
    unassigned_scenes = _db.fetchall(
        """
        SELECT DISTINCT scene_id
          FROM cross_encoder_labels
         WHERE split IS NULL
           AND label IN ('positive', 'negative')
         ORDER BY scene_id
        """
    )
    if not unassigned_scenes:
        return

    scene_ids = [r["scene_id"] for r in unassigned_scenes]
    random.shuffle(scene_ids)

    n_test    = max(1, math.ceil(len(scene_ids) * TEST_RATIO))
    test_ids  = scene_ids[:n_test]
    train_ids = scene_ids[n_test:]

    if train_ids:
        _db.execute(
            "UPDATE cross_encoder_labels SET split = 'train' "
            "WHERE scene_id = ANY(%s) AND split IS NULL",
            [train_ids],
        )
    if test_ids:
        _db.execute(
            "UPDATE cross_encoder_labels SET split = 'test' "
            "WHERE scene_id = ANY(%s) AND split IS NULL",
            [test_ids],
        )

    logger.info(
        "Split 신규 할당: total_scenes=%d → train_scenes=%d, test_scenes=%d",
        len(scene_ids), len(train_ids), len(test_ids),
    )


# ── 학습 이력 ──────────────────────────────────────────────────────────────────

def _create_training_run(epochs: int, model_path: str) -> int:
    """ce_training_runs에 학습 시작 row 생성 후 run_id 반환."""
    rows = _db.fetchall(
        """
        INSERT INTO ce_training_runs (epochs, model_path)
        VALUES (%s, %s)
        RETURNING id
        """,
        [epochs, model_path],
    )
    run_id = rows[0]["id"]
    logger.info("Training run 시작 기록: run_id=%d", run_id)
    return run_id


def _finalize_training_run(run_id: int, train_count: int, test_count: int) -> None:
    """학습 완료 후 ce_training_runs 실제 건수 업데이트."""
    _db.execute(
        """
        UPDATE ce_training_runs
           SET train_count = %s,
               test_count  = %s
         WHERE id = %s
        """,
        [train_count, test_count, run_id],
    )
    logger.info(
        "Training run 완료 기록: run_id=%d, train=%d, test=%d",
        run_id, train_count, test_count,
    )


def _mark_trained_at() -> None:
    """split='train' 행 전체에 trained_at = NOW() 기록."""
    _db.execute(
        "UPDATE cross_encoder_labels SET trained_at = NOW() WHERE split = 'train'"
    )
    logger.info("cross_encoder_labels.trained_at 업데이트 완료")


# ── DB 조회 ────────────────────────────────────────────────────────────────────

def _load_train_data(neg_ratio: int = 3) -> tuple[list[dict], list[dict]]:
    """
    split 컬럼 기준으로 train/test 데이터를 로드.

    1. split=NULL 행이 있으면 scene_id 단위로 80/20 할당 (DB 영구 저장)
    2. split='train' 행 로드 → negative 다운샘플링 적용
    3. split='test'  행 로드 → 평가용 (다운샘플링 없음)

    Returns:
        (train_rows, test_rows)
    """
    # 1. 신규 라벨 split 할당
    _assign_split_if_needed()

    # 2. train 로드
    train_pos = _db.fetchall(
        "SELECT scene_id, context_narrative, target_narrative, gemini_score "
        "FROM cross_encoder_labels WHERE split = 'train' AND label = 'positive' ORDER BY id"
    )
    train_neg = _db.fetchall(
        "SELECT scene_id, context_narrative, target_narrative, gemini_score "
        "FROM cross_encoder_labels WHERE split = 'train' AND label = 'negative' ORDER BY id"
    )

    max_neg = len(train_pos) * neg_ratio
    if len(train_neg) > max_neg:
        train_neg = random.sample(train_neg, max_neg)
        logger.info(
            "Train negative 다운샘플링: %d → %d (ratio 1:%d)",
            len(train_neg), max_neg, neg_ratio,
        )

    train_rows = train_pos + train_neg
    random.shuffle(train_rows)

    # 3. test 로드
    test_rows = _db.fetchall(
        "SELECT scene_id, context_narrative, target_narrative, gemini_score, label "
        "FROM cross_encoder_labels WHERE split = 'test' AND label IN ('positive', 'negative') ORDER BY id"
    )

    logger.info(
        "Train: positive=%d, negative=%d / total=%d",
        len(train_pos), len(train_neg), len(train_rows),
    )
    logger.info(
        "Test : positive=%d, negative=%d / total=%d",
        sum(1 for r in test_rows if r.get("label") == "positive"),
        sum(1 for r in test_rows if r.get("label") == "negative"),
        len(test_rows),
    )

    return train_rows, test_rows


# ── 학습 ──────────────────────────────────────────────────────────────────────

def run(epochs: int = 3, output_dir: str = DEFAULT_OUTPUT_DIR, neg_ratio: int = 3) -> None:
    try:
        from sentence_transformers import CrossEncoder, InputExample
        from sentence_transformers.cross_encoder.evaluation import CERerankingEvaluator
        from torch.utils.data import DataLoader
    except ImportError:
        logger.error("sentence-transformers가 설치되지 않았습니다. pip install sentence-transformers")
        sys.exit(1)

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

    # 학습 이력 시작 기록
    run_id = _create_training_run(epochs=epochs, model_path=str(output_path))

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

    # test set 평가용 — scene(context) 기준으로 positive/negative 묶기
    from collections import defaultdict
    scene_pos: dict = defaultdict(list)
    scene_neg: dict = defaultdict(list)
    for s in test_samples:
        if s.label >= 0.7:
            scene_pos[s.texts[0]].append(s.texts[1])
        else:
            scene_neg[s.texts[0]].append(s.texts[1])

    eval_samples = [
        {"query": q, "positive": pos, "negative": scene_neg.get(q, [])}
        for q, pos in scene_pos.items()
        if scene_neg.get(q)
    ]
    evaluator = CERerankingEvaluator(samples=eval_samples, name="test") if eval_samples else None
    if evaluator:
        logger.info("Evaluator: %d scene queries", len(eval_samples))
    else:
        logger.warning("Evaluator 구성 불가 — test set에 positive/negative 쌍이 부족합니다.")

    logger.info(
        "Training start — epochs=%d, train=%d, test=%d, output=%s",
        epochs, len(train_samples), len(test_samples), output_path,
    )

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

    # 학습 완료 후 이력 기록
    _finalize_training_run(run_id, len(train_samples), len(test_samples))
    _mark_trained_at()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cross-Encoder Fine-tuner")
    parser.add_argument("--epochs",     type=int, default=3)
    parser.add_argument("--output-dir", type=str, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--neg-ratio",  type=int, default=3, help="positive 1건당 negative 최대 비율 (기본 1:3)")
    args = parser.parse_args()

    run(epochs=args.epochs, output_dir=args.output_dir, neg_ratio=args.neg_ratio)
