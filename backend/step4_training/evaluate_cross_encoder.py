"""
step4_training/evaluate_cross_encoder.py — Cross-Encoder 평가 (Before vs After)
────────────────────────────────────────────────────────────────────────────────
cross_encoder_labels 테이블의 holdout 데이터로
베이스 모델(BAAI/bge-reranker-base) vs 파인튜닝 모델을 비교 평가.

메트릭:
  - MRR@K  (Mean Reciprocal Rank)  : 씬당 positive가 상위 몇 위에 있나
  - P@K    (Precision@K)           : 상위 K개 중 positive 비율
  - NDCG@K (Normalized DCG)        : 순위 가중 정밀도

실행:
    python -m step4_training.evaluate_cross_encoder
    python -m step4_training.evaluate_cross_encoder --top-k 5 --holdout-ratio 0.2
    python -m step4_training.evaluate_cross_encoder --finetuned-dir /app/storage/models/cross_encoder

저장 경로:
    /app/storage/logs/eval_cross_encoder_<timestamp>.json
"""

import argparse
import json
import logging
import math
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from common import db as _db
from common.logging_setup import setup_logging

setup_logging("evaluate_cross_encoder")
logger = logging.getLogger(__name__)

BASE_MODEL         = "BAAI/bge-reranker-base"
DEFAULT_MODEL_DIR  = "/app/storage/models/cross_encoder"
DEFAULT_TOP_K      = 10
DEFAULT_HOLDOUT    = 0.2   # 전체 데이터의 20%를 평가용으로 사용
RESULT_DIR         = Path("/app/storage/logs")


# ── 데이터 로드 ────────────────────────────────────────────────────────────────

def _load_eval_data(holdout_ratio: float, seed: int = 42) -> list[dict]:
    """
    cross_encoder_labels에서 씬 단위 holdout 데이터를 구성한다.

    씬 단위로 분리하는 이유:
      - 같은 씬의 데이터가 train/eval 양쪽에 들어가면 데이터 누수 발생
      - 씬 ID 기준으로 분리해야 공정한 평가

    반환 형식 (씬 단위 그룹):
      [
        {
          "scene_id": int,
          "context_narrative": str,
          "candidates": [
            {"target_narrative": str, "label": "positive"|"negative", "gemini_score": float},
            ...
          ]
        },
        ...
      ]
    평가 가능 조건: 씬당 positive >= 1, negative >= 1
    """
    rows = _db.fetchall(
        """
        SELECT scene_id, context_narrative, target_narrative, label, gemini_score
          FROM cross_encoder_labels
         WHERE label IN ('positive', 'negative')
         ORDER BY scene_id, id
        """
    )
    if not rows:
        logger.error("cross_encoder_labels에 positive/negative 데이터가 없습니다.")
        sys.exit(1)

    # 씬별 그룹핑
    by_scene: dict[int, dict] = {}
    for r in rows:
        sid = r["scene_id"]
        if sid not in by_scene:
            by_scene[sid] = {
                "scene_id": sid,
                "context_narrative": r["context_narrative"],
                "candidates": [],
            }
        by_scene[sid]["candidates"].append({
            "target_narrative": r["target_narrative"],
            "label":            r["label"],
            "gemini_score":     float(r["gemini_score"]),
        })

    # 평가 가능한 씬만 필터 (positive + negative 모두 있어야 함)
    valid_scenes = [
        s for s in by_scene.values()
        if any(c["label"] == "positive" for c in s["candidates"])
        and any(c["label"] == "negative" for c in s["candidates"])
    ]

    if not valid_scenes:
        logger.error(
            "평가 가능한 씬이 없습니다. "
            "씬당 positive와 negative가 모두 필요합니다."
        )
        sys.exit(1)

    # 씬 단위 holdout 분리
    random.seed(seed)
    random.shuffle(valid_scenes)
    holdout_n = max(1, math.ceil(len(valid_scenes) * holdout_ratio))
    eval_scenes = valid_scenes[:holdout_n]

    logger.info(
        "전체 valid 씬: %d | holdout 평가 씬: %d (ratio=%.0f%%)",
        len(valid_scenes), len(eval_scenes), holdout_ratio * 100,
    )
    return eval_scenes


# ── 모델 추론 ──────────────────────────────────────────────────────────────────

def _load_model(model_path: str):
    """CrossEncoder 모델 로드. model_path가 디렉토리면 로컬, 아니면 HuggingFace."""
    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        logger.error("sentence-transformers 미설치. pip install sentence-transformers")
        sys.exit(1)

    logger.info("모델 로드 중: %s", model_path)
    model = CrossEncoder(model_path, num_labels=1)
    logger.info("모델 로드 완료.")
    return model


def _predict_scores(model, scenes: list[dict]) -> list[dict]:
    """
    각 씬의 모든 (context, target) 쌍을 배치 추론.
    반환: [{"scene_id", "context_narrative", "ranked": [{"target_narrative", "label", "score"}, ...]}, ...]
    """
    import numpy as np

    # 전체 쌍을 한 번에 배치로 처리
    all_pairs = []
    pair_index = []  # (scene_idx, candidate_idx)

    for s_idx, scene in enumerate(scenes):
        ctx = scene["context_narrative"]
        for c_idx, cand in enumerate(scene["candidates"]):
            all_pairs.append([ctx, cand["target_narrative"]])
            pair_index.append((s_idx, c_idx))

    logger.info("배치 추론 중 — %d 쌍 ...", len(all_pairs))
    raw_scores = model.predict(all_pairs, show_progress_bar=True)

    # sigmoid 정규화 (ms-marco 계열은 로짓 출력)
    def sigmoid(x):
        return float(1 / (1 + np.exp(-float(x))))

    scores_by_scene: dict[int, list] = defaultdict(list)
    for (s_idx, c_idx), raw in zip(pair_index, raw_scores):
        scene = scenes[s_idx]
        cand  = scene["candidates"][c_idx]
        scores_by_scene[s_idx].append({
            "target_narrative": cand["target_narrative"],
            "label":            cand["label"],
            "gemini_score":     cand["gemini_score"],
            "score":            sigmoid(raw),
        })

    results = []
    for s_idx, scene in enumerate(scenes):
        ranked = sorted(scores_by_scene[s_idx], key=lambda x: x["score"], reverse=True)
        results.append({
            "scene_id":           scene["scene_id"],
            "context_narrative":  scene["context_narrative"],
            "ranked":             ranked,
        })
    return results


# ── 메트릭 계산 ────────────────────────────────────────────────────────────────

def _reciprocal_rank(ranked: list[dict]) -> float:
    """첫 번째 positive의 순위 역수. positive 없으면 0.0."""
    for rank, item in enumerate(ranked, start=1):
        if item["label"] == "positive":
            return 1.0 / rank
    return 0.0


def _precision_at_k(ranked: list[dict], k: int) -> float:
    """상위 k개 중 positive 비율."""
    top_k = ranked[:k]
    positives = sum(1 for item in top_k if item["label"] == "positive")
    return positives / k


def _ndcg_at_k(ranked: list[dict], k: int) -> float:
    """
    NDCG@K — gemini_score를 relevance로 사용.
    이상적인 순서(내림차순)와 실제 순서를 비교.
    """
    def dcg(items):
        score = 0.0
        for i, item in enumerate(items[:k], start=1):
            rel = item["gemini_score"]
            score += rel / math.log2(i + 1)
        return score

    actual_dcg = dcg(ranked)
    ideal = sorted(ranked, key=lambda x: x["gemini_score"], reverse=True)
    ideal_dcg = dcg(ideal)

    return actual_dcg / ideal_dcg if ideal_dcg > 0 else 0.0


def _compute_metrics(results: list[dict], k: int) -> dict:
    """씬 전체의 메트릭 평균을 계산한다."""
    mrr_list, p_at_k_list, ndcg_list = [], [], []

    for r in results:
        ranked = r["ranked"]
        mrr_list.append(_reciprocal_rank(ranked))
        p_at_k_list.append(_precision_at_k(ranked, k))
        ndcg_list.append(_ndcg_at_k(ranked, k))

    return {
        f"MRR@{k}":   round(sum(mrr_list)   / len(mrr_list),   4),
        f"P@{k}":     round(sum(p_at_k_list) / len(p_at_k_list), 4),
        f"NDCG@{k}":  round(sum(ndcg_list)  / len(ndcg_list),  4),
        "n_scenes":   len(results),
    }


# ── 리포트 출력 ────────────────────────────────────────────────────────────────

def _print_report(base_metrics: dict, ft_metrics: dict, k: int) -> None:
    """Before / After 비교 테이블 출력."""
    sep = "─" * 52

    print(f"\n{'═' * 52}")
    print(f"  Cross-Encoder 평가 결과  (K={k})")
    print(f"{'═' * 52}")
    print(f"  {'메트릭':<12} {'베이스 모델':>14} {'파인튜닝 후':>14}")
    print(sep)

    for key in [f"MRR@{k}", f"P@{k}", f"NDCG@{k}"]:
        base_val = base_metrics.get(key, 0.0)
        ft_val   = ft_metrics.get(key, 0.0)
        diff     = ft_val - base_val
        arrow    = "▲" if diff > 0 else ("▼" if diff < 0 else "─")
        diff_str = f"{arrow} {abs(diff):.4f}"
        print(f"  {key:<12} {base_val:>14.4f} {ft_val:>14.4f}   {diff_str}")

    print(sep)
    print(f"  평가 씬 수: {base_metrics['n_scenes']}")
    print(f"{'═' * 52}\n")

    # 해석 가이드
    mrr_key = f"MRR@{k}"
    diff_mrr = ft_metrics.get(mrr_key, 0) - base_metrics.get(mrr_key, 0)
    if diff_mrr > 0.05:
        print(f"  ✓ MRR이 {diff_mrr:.4f} 향상 — 파인튜닝 효과가 뚜렷합니다.")
    elif diff_mrr > 0:
        print(f"  ○ MRR이 {diff_mrr:.4f} 소폭 향상 — 데이터를 더 모아보세요.")
    elif diff_mrr < -0.02:
        print(f"  ✗ MRR이 {abs(diff_mrr):.4f} 하락 — 과적합 또는 데이터 품질 확인 필요.")
    else:
        print(f"  ─ MRR 변화 미미 — 라벨 수를 늘리거나 epoch을 조정해보세요.")
    print()


def _save_result(base_metrics: dict, ft_metrics: dict, k: int, ft_model_dir: str) -> None:
    """평가 결과를 JSON으로 저장."""
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULT_DIR / f"eval_cross_encoder_{timestamp}.json"

    result = {
        "evaluated_at":    timestamp,
        "top_k":           k,
        "base_model":      BASE_MODEL,
        "finetuned_model": ft_model_dir,
        "base":            base_metrics,
        "finetuned":       ft_metrics,
        "improvement": {
            key: round(ft_metrics[key] - base_metrics[key], 4)
            for key in [f"MRR@{k}", f"P@{k}", f"NDCG@{k}"]
        },
    }

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    logger.info("평가 결과 저장: %s", out_path)


# ── 메인 ──────────────────────────────────────────────────────────────────────

def run(
    top_k: int = DEFAULT_TOP_K,
    holdout_ratio: float = DEFAULT_HOLDOUT,
    finetuned_dir: str = DEFAULT_MODEL_DIR,
    seed: int = 42,
    skip_base: bool = False,
) -> None:
    # 1. 평가 데이터 로드
    eval_scenes = _load_eval_data(holdout_ratio=holdout_ratio, seed=seed)

    # 2. 파인튜닝 모델 존재 여부 확인
    ft_path = Path(finetuned_dir)
    if not ft_path.exists():
        logger.error(
            "파인튜닝 모델이 없습니다: %s\n"
            "먼저 python -m step4_training.train_cross_encoder 를 실행하세요.",
            finetuned_dir,
        )
        sys.exit(1)

    # 3. 베이스 모델 평가
    base_metrics = {}
    if not skip_base:
        logger.info("── 베이스 모델 평가 시작 ──")
        base_model   = _load_model(BASE_MODEL)
        base_results = _predict_scores(base_model, eval_scenes)
        base_metrics = _compute_metrics(base_results, top_k)
        logger.info("베이스 메트릭: %s", base_metrics)
        del base_model  # 메모리 해제
    else:
        logger.info("베이스 모델 평가 건너뜀 (--skip-base)")
        base_metrics = {f"MRR@{top_k}": 0.0, f"P@{top_k}": 0.0, f"NDCG@{top_k}": 0.0, "n_scenes": len(eval_scenes)}

    # 4. 파인튜닝 모델 평가
    logger.info("── 파인튜닝 모델 평가 시작 ──")
    ft_model   = _load_model(finetuned_dir)
    ft_results = _predict_scores(ft_model, eval_scenes)
    ft_metrics = _compute_metrics(ft_results, top_k)
    logger.info("파인튜닝 메트릭: %s", ft_metrics)

    # 5. 리포트 출력 및 저장
    _print_report(base_metrics, ft_metrics, top_k)
    _save_result(base_metrics, ft_metrics, top_k, finetuned_dir)

    # 6. 씬별 상세 결과 (상위 5개 씬 출력)
    print("── 씬별 상세 예시 (파인튜닝 모델 기준 상위 5개) ──")
    for scene_result in ft_results[:5]:
        print(f"\n  씬 {scene_result['scene_id']}: {scene_result['context_narrative'][:60]}...")
        for i, item in enumerate(scene_result["ranked"][:5], start=1):
            mark = "✓" if item["label"] == "positive" else "✗"
            print(
                f"    {i}위 {mark} score={item['score']:.3f} "
                f"gemini={item['gemini_score']:.2f}  "
                f"{item['target_narrative'][:50]}..."
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cross-Encoder Before/After 평가")
    parser.add_argument("--top-k",         type=int,   default=DEFAULT_TOP_K,
                        help=f"평가 K값 (기본: {DEFAULT_TOP_K})")
    parser.add_argument("--holdout-ratio", type=float, default=DEFAULT_HOLDOUT,
                        help=f"holdout 비율 (기본: {DEFAULT_HOLDOUT})")
    parser.add_argument("--finetuned-dir", type=str,   default=DEFAULT_MODEL_DIR,
                        help=f"파인튜닝 모델 경로 (기본: {DEFAULT_MODEL_DIR})")
    parser.add_argument("--seed",          type=int,   default=42,
                        help="랜덤 시드 (기본: 42)")
    parser.add_argument("--skip-base",     action="store_true",
                        help="베이스 모델 평가 생략 (시간 절약, 파인튜닝 모델만 평가)")
    args = parser.parse_args()

    run(
        top_k=args.top_k,
        holdout_ratio=args.holdout_ratio,
        finetuned_dir=args.finetuned_dir,
        seed=args.seed,
        skip_base=args.skip_base,
    )
