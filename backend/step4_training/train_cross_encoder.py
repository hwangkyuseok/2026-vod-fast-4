"""
step4_training/train_cross_encoder.py — Cross-Encoder Fine-tuning
──────────────────────────────────────────────────────────────────
cross_encoder_labels 테이블의 positive/negative 라벨 데이터로
BAAI/bge-reranker-base를 Fine-tuning하여 로컬에 저장.

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
import os
import random
import sys
from pathlib import Path

# --use-cpu 플래그를 torch import 전에 미리 감지해서 MPS/CUDA 비활성화
if "--use-cpu" in sys.argv:
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["ACCELERATE_USE_MPS_DEVICE"] = "false"
    os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "0"

sys.path.insert(0, str(Path(__file__).parent.parent))

from common import db as _db
from common.logging_setup import setup_logging

setup_logging("train_cross_encoder")
logger = logging.getLogger(__name__)

BASE_MODEL         = "BAAI/bge-reranker-base"
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
         WHERE split_v2 IS NULL
           AND label IN ('positive', 'negative', 'ambiguous')
         ORDER BY scene_id
        """
    )
    if not unassigned_scenes:
        return

    scene_ids = [r["scene_id"] for r in unassigned_scenes]
    random.seed(42)
    random.shuffle(scene_ids)

    n_test    = max(1, math.ceil(len(scene_ids) * TEST_RATIO))
    test_ids  = scene_ids[:n_test]
    train_ids = scene_ids[n_test:]

    if train_ids:
        _db.execute(
            "UPDATE cross_encoder_labels SET split_v2 = 'train'"
            "WHERE scene_id = ANY(%s) AND split_v2 IS NULL",
            [train_ids],
        )
    if test_ids:
        _db.execute(
            "UPDATE cross_encoder_labels SET split_v2 = 'test' "
            "WHERE scene_id = ANY(%s) AND split_v2 IS NULL",
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
        "UPDATE cross_encoder_labels SET trained_at = NOW() WHERE split_v2 = 'train'"
    )
    logger.info("cross_encoder_labels.trained_at 업데이트 완료")


# ── DB 조회 ────────────────────────────────────────────────────────────────────

def _load_train_data(neg_ratio: int = 3, include_ambiguous: bool = False) -> tuple[list[dict], list[dict]]:
    """
    split 컬럼 기준으로 train/test 데이터를 로드.

    1. split=NULL 행이 있으면 scene_id 단위로 80/20 할당 (DB 영구 저장)
    2. split='train' 행 로드 → negative 다운샘플링 적용
    3. split='test'  행 로드 → 평가용 (다운샘플링 없음)

    Args:
        neg_ratio: positive 1건당 negative 최대 비율
        include_ambiguous: True이면 ambiguous(gemini_score 0.3~0.5)를 hard negative로 포함

    Returns:
        (train_rows, test_rows)
    """
    # 1. 신규 라벨 split 할당
    _assign_split_if_needed()

    # 2. train 로드
    train_pos = _db.fetchall(
        "SELECT scene_id, context_narrative, target_narrative, gemini_score "
        "FROM cross_encoder_labels WHERE split_v2 = 'train' AND label = 'positive' ORDER BY id"
    )

    if include_ambiguous:
        train_neg = _db.fetchall(
            "SELECT scene_id, context_narrative, target_narrative, gemini_score "
            "FROM cross_encoder_labels WHERE split_v2 = 'train' AND label IN ('negative', 'ambiguous') AND gemini_score <= 0.5 ORDER BY id"
        )
        logger.info("Hard negative 모드: ambiguous(score<=0.5) 포함하여 negative 로드")
    else:
        train_neg = _db.fetchall(
            "SELECT scene_id, context_narrative, target_narrative, gemini_score "
            "FROM cross_encoder_labels WHERE split_v2 = 'train' AND label = 'negative' ORDER BY id"
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
        "FROM cross_encoder_labels WHERE split_v2 = 'test' AND label IN ('positive', 'negative') ORDER BY id"
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

def run(
    epochs: int = 3,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    neg_ratio: int = 3,
    base_model: str = BASE_MODEL,
    include_ambiguous: bool = True,
    use_cpu: bool = False,
) -> None:
    try:
        from sentence_transformers import CrossEncoder, InputExample
        from sentence_transformers.cross_encoder.evaluation import CERerankingEvaluator
        from torch.utils.data import DataLoader
    except ImportError:
        logger.error("sentence-transformers가 설치되지 않았습니다. pip install sentence-transformers")
        sys.exit(1)

    if use_cpu:
        import torch
        torch.backends.mps.is_available = lambda: False
        torch.backends.mps.is_built = lambda: False
        logger.info("CPU 모드로 실행 중 (MPS 비활성화)")

    train_rows, test_rows = _load_train_data(neg_ratio=neg_ratio, include_ambiguous=include_ambiguous)
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
    device = "cpu" if use_cpu else None  # None = 자동 감지

    existing = sorted(checkpoint_path.glob("*-steps")) if checkpoint_path.exists() else []
    if existing:
        resume_from = str(existing[-1])
        logger.info("Resuming from checkpoint: %s", resume_from)
        model = CrossEncoder(resume_from, num_labels=1, device=device)
    else:
        logger.info("Base model: %s", base_model)
        model = CrossEncoder(base_model, num_labels=1, device=device)

    train_dataloader = DataLoader(train_samples, shuffle=True, batch_size=4)

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

    # ── epoch별 train loss / eval 비교 학습 루프 ───────────────────────────────
    import torch
    from tqdm import tqdm
    from transformers import get_linear_schedule_with_warmup

    train_dataloader.collate_fn = model.smart_batching_collate
    loss_fn   = torch.nn.BCEWithLogitsLoss()
    optimizer = torch.optim.AdamW(model.model.parameters(), lr=2e-5, weight_decay=0.01)

    total_steps  = len(train_dataloader) * epochs
    warmup_steps = max(1, len(train_dataloader) // 10)
    scheduler    = get_linear_schedule_with_warmup(optimizer, warmup_steps, total_steps)
    device_obj   = next(model.model.parameters()).device

    prev_eval = None
    best_eval = None
    best_epoch = None
    best_model_path = output_path / "best_model"
    for epoch in range(1, epochs + 1):
        model.model.train()
        epoch_loss = 0.0

        for features, labels in tqdm(train_dataloader, desc=f"Epoch {epoch}/{epochs}", leave=False):
            labels = labels.to(device_obj)
            optimizer.zero_grad()
            outputs = model.model(**{k: v.to(device_obj) for k, v in features.items()})
            logits  = outputs.logits.squeeze(-1)
            loss    = loss_fn(logits, labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            epoch_loss += loss.item()

        avg_train_loss = epoch_loss / len(train_dataloader)

        eval_score = None
        if evaluator:
            model.model.eval()
            result = evaluator(model, output_path=str(output_path))
            # result가 dict이면 MRR@10 추출, float이면 그대로 사용
            if isinstance(result, dict):
                eval_score = result.get("mrr@10") or result.get("MRR@10") or next(iter(result.values()))
            else:
                eval_score = result

        if prev_eval is not None and eval_score is not None:
            delta = eval_score - prev_eval
            indicator = f"eval 증가 ✓ (+{delta:.4f})" if delta > 0 else f"eval 감소 ✗ ({delta:.4f}) ← 과적합 의심"
        else:
            indicator = ""

        # best model 저장
        if eval_score is not None and (best_eval is None or eval_score > best_eval):
            best_eval = eval_score
            best_epoch = epoch
            model.save(str(best_model_path))
            logger.info("Best model 저장: epoch=%d, eval_mrr=%.4f → %s", epoch, eval_score, best_model_path)

        logger.info(
            "Epoch %d/%d  train_loss: %.4f  eval_mrr: %s  %s",
            epoch, epochs,
            avg_train_loss,
            f"{eval_score:.4f}" if eval_score is not None else "N/A",
            indicator,
        )
        prev_eval = eval_score

    logger.info("Best epoch: %d, eval_mrr: %.4f, saved to: %s", best_epoch, best_eval, best_model_path)

    model.save(str(output_path))
    logger.info("Fine-tuning complete. Model saved to: %s", output_path)

    # 학습 완료 후 이력 기록
    _finalize_training_run(run_id, len(train_samples), len(test_samples))
    _mark_trained_at()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cross-Encoder Fine-tuner")
    parser.add_argument("--epochs",            type=int,  default=3)
    parser.add_argument("--output-dir",        type=str,  default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--neg-ratio",         type=int,  default=3, help="positive 1건당 negative 최대 비율 (기본 1:3)")
    parser.add_argument("--base-model",        type=str,  default=BASE_MODEL, help=f"베이스 모델 (기본: {BASE_MODEL})")
    parser.add_argument("--no-ambiguous", action="store_false", dest="include_ambiguous", default=True, help="ambiguous 라벨을 hard negative에서 제외 (기본: 포함)")
    parser.add_argument("--use-cpu",           action="store_true", help="MPS/GPU 대신 CPU 강제 사용")
    args = parser.parse_args()

    run(
        epochs=args.epochs,
        output_dir=args.output_dir,
        neg_ratio=args.neg_ratio,
        base_model=args.base_model,
        include_ambiguous=args.include_ambiguous,
        use_cpu=args.use_cpu,
    )
