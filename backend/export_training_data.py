"""
export_training_data.py
────────────────────────────────────────────────────────────────
ad_placement_feedback 테이블에 수집된 라벨 데이터를
CatBoost 학습용 CSV로 내보내는 스크립트.

출력 컬럼:
  피처 (X):
    similarity_score     - context ↔ target 코사인 유사도 (0~1)
    final_score          - Step4 복합 점수
    scene_duration_sec   - 씬 길이 (초)
    ad_duration_sec      - 광고 길이 (초, 배너는 null)
    avg_density          - 최적 윈도우 평균 객체 밀도 (0~1)
    ad_type              - 'video_clip' | 'banner'  ← CatBoost 범주형 피처

  레이블 (y):
    label                - -1=부적합, 0=보통, 1=적합

실행:
    python export_training_data.py
    python export_training_data.py --output my_data.csv
    python export_training_data.py --min-label-count 10   # 라벨 10개 이상 수집 시 실행
"""

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import db as _db
from common.logging_setup import setup_logging

setup_logging("export_training_data")


QUERY = """
SELECT
    dr.similarity_score,
    dr.score          AS final_score,
    dr.scene_duration_sec,
    ai.duration_sec   AS ad_duration_sec,
    dr.avg_density,
    ai.ad_type,
    f.label
  FROM ad_placement_feedback f
  JOIN decision_result  dr ON dr.id    = f.decision_id
  JOIN ad_inventory     ai ON ai.ad_id = dr.ad_id
 WHERE dr.similarity_score IS NOT NULL
 ORDER BY f.created_at
"""

COLUMNS = [
    "similarity_score",
    "final_score",
    "scene_duration_sec",
    "ad_duration_sec",
    "avg_density",
    "ad_type",
    "label",
]

# CatBoost 학습 시 범주형으로 지정해야 할 컬럼
CAT_FEATURES = ["ad_type"]


def export(output_path: str, min_count: int) -> None:
    rows = _db.fetchall(QUERY)

    if len(rows) < min_count:
        print(
            f"⚠  수집된 라벨 수: {len(rows)}개 (최소 {min_count}개 필요). 수집을 더 진행하세요."
        )
        return

    dist = {-1: 0, 0: 0, 1: 0}
    for r in rows:
        dist[r["label"]] += 1

    path = Path(output_path)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow({col: r[col] for col in COLUMNS})

    print(f"✓ {len(rows)}개 레코드 → {path}")
    print(f"  라벨 분포: 적합={dist[1]}, 보통={dist[0]}, 부적합={dist[-1]}")
    print(f"  범주형 피처: {CAT_FEATURES}")
    print()
    print("── CatBoost 학습 예시 코드 ────────────────────────────────────")
    print(f"""
import pandas as pd
from catboost import CatBoostClassifier

df = pd.read_csv("{path}")
X = df.drop(columns=["label"])
y = df["label"]

model = CatBoostClassifier(
    iterations=500,
    learning_rate=0.05,
    depth=6,
    cat_features={CAT_FEATURES},
    loss_function="MultiClass",   # -1, 0, 1 다중분류
    eval_metric="Accuracy",
    verbose=50,
)
model.fit(X, y, eval_set=(X, y))
model.save_model("ad_matching_model.cbm")
""")


def main() -> None:
    parser = argparse.ArgumentParser(description="CatBoost 학습 데이터 내보내기")
    parser.add_argument("--output", default="training_data.csv", help="출력 CSV 파일 경로")
    parser.add_argument("--min-label-count", type=int, default=30,
                        help="최소 라벨 수 (기본값: 30)")
    args = parser.parse_args()
    export(args.output, args.min_label_count)


if __name__ == "__main__":
    main()
