import warnings
warnings.filterwarnings('ignore')

import time
import numpy as np
import pandas as pd
from pathlib import Path
from ultralytics import YOLO

# =========================
# 1. 기본 설정
# =========================
MODEL_PATH = "yolov8l.pt"
VIDEO_PATH = "SampleVideo_Scenes"
DATA_YAML = "coco128.yaml"
REPORT_PATH = Path("Project_Report.md")
RESULT_TXT = "tune_conf_results.txt"

# MVP 15 class ids
MVP_CLASS_IDS = [16, 26, 39, 41, 45, 53, 56, 57, 59, 60, 62, 63, 65, 67, 72]

# 고정 파라미터
FIXED_IOU = 0.7
FIXED_IMGSZ = 640

# conf 실험 범위: 0.15 ~ 0.70 (0.05 간격)
CONF_VALUES = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]

# =========================
# 2. 모델 로드
# =========================
print("Loading YOLOv8l model...")
model = YOLO(MODEL_PATH)

rows = []
scene_files = sorted(Path(VIDEO_PATH).glob("*.mp4"))
print(f"샘플 scene clip 수: {len(scene_files)}")

# =========================
# 3. 실험 실행
# =========================
for exp_idx, conf_val in enumerate(CONF_VALUES, start=1):
    print(f"\n{'='*50}")
    print(f"  실험 C{exp_idx}: conf={conf_val}, iou={FIXED_IOU}, imgsz={FIXED_IMGSZ}")
    print(f"{'='*50}")

    # 3-1. COCO128 Validation (MVP 15 클래스 기준)
    print("  [1/2] COCO128 Validation 수행 중...")
    metrics = model.val(
        data=DATA_YAML,
        imgsz=FIXED_IMGSZ,
        conf=conf_val,
        iou=FIXED_IOU,
        classes=MVP_CLASS_IDS,
        verbose=False
    )

    precision_arr = metrics.box.p
    recall_arr = metrics.box.r

    precision = float(np.nanmean(precision_arr)) if hasattr(precision_arr, "__len__") else float(precision_arr)
    recall = float(np.nanmean(recall_arr)) if hasattr(recall_arr, "__len__") else float(recall_arr)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    print(f"  Precision={precision:.4f}, Recall={recall:.4f}, F1={f1:.4f}")

    # 3-2. SampleVideo_Scenes 추론 (탐지 수 + 소요 시간)
    print(f"  [2/2] SampleVideo_Scenes 추론 중 ({len(scene_files)} clips)...")
    total_detected = 0
    start_time = time.time()

    for idx, scene_file in enumerate(scene_files, start=1):
        results = model.predict(
            source=str(scene_file),
            conf=conf_val,
            iou=FIXED_IOU,
            imgsz=FIXED_IMGSZ,
            classes=MVP_CLASS_IDS,
            verbose=False
        )
        for result in results:
            if result.boxes is not None:
                total_detected += len(result.boxes)

        if idx % 50 == 0:
            print(f"    ... {idx}/{len(scene_files)} 완료")

    elapsed = time.time() - start_time
    det_per_sec = total_detected / elapsed if elapsed > 0 else 0

    print(f"  총 탐지 수: {total_detected}, 소요 시간: {elapsed:.1f}s, 탐지/sec: {det_per_sec:.1f}")

    rows.append({
        "실험": f"C{exp_idx}",
        "conf": conf_val,
        "iou": FIXED_IOU,
        "imgsz": FIXED_IMGSZ,
        "Precision": round(precision, 4),
        "Recall": round(recall, 4),
        "F1": round(f1, 4),
        "탐지 수": total_detected,
        "소요 시간(s)": round(elapsed, 1),
        "탐지/sec": round(det_per_sec, 1)
    })

df = pd.DataFrame(rows)

# =========================
# 4. 결과 텍스트 파일 저장
# =========================
with open(RESULT_TXT, "w", encoding="utf-8") as f:
    f.write("=== conf 파라미터 튜닝 실험 결과 ===\n")
    f.write(f"모델: {MODEL_PATH} | 클래스: MVP 15 | iou: {FIXED_IOU} | imgsz: {FIXED_IMGSZ}\n\n")
    f.write(df.to_string(index=False))
    f.write("\n")

print(f"\n결과 저장 완료: {RESULT_TXT}")

# =========================
# 5. Project_Report.md 에 섹션 추가
# =========================
section_lines = []
section_lines.append("\n\n## 4. Inference 파라미터 튜닝 - conf (Confidence Threshold)\n")
section_lines.append("\n")
section_lines.append("YOLOv8L + MVP 15 클래스 기준으로 conf(Confidence Threshold) 값을 변경하며 정확도와 탐지량 변화를 측정하였다.\n")
section_lines.append("\n")
section_lines.append("### 실험 설정\n")
section_lines.append("\n")
section_lines.append("| 항목 | 설정 |\n")
section_lines.append("| --- | --- |\n")
section_lines.append(f"| 모델 | {MODEL_PATH} |\n")
section_lines.append(f"| 클래스 범위 | MVP 15 |\n")
section_lines.append(f"| 검증 데이터셋 | {DATA_YAML} (Ground Truth 기반) |\n")
section_lines.append(f"| 샘플 영상 | {VIDEO_PATH} ({len(scene_files)} scene clips) |\n")
section_lines.append(f"| 고정 파라미터 | iou={FIXED_IOU}, imgsz={FIXED_IMGSZ} |\n")
section_lines.append(f"| 실험 변수 | conf = {CONF_VALUES[0]} ~ {CONF_VALUES[-1]} (0.05 간격, {len(CONF_VALUES)}회) |\n")
section_lines.append("\n")
section_lines.append("### conf 튜닝 결과\n")
section_lines.append("\n")
section_lines.append(df.to_markdown(index=False))
section_lines.append("\n\n")
section_lines.append("### 결과 해석\n")
section_lines.append("\n")
section_lines.append("- conf가 낮을수록 Recall(재현율)이 높아지지만 Precision(정밀도)이 낮아진다 (오탐 증가).\n")
section_lines.append("- conf가 높을수록 Precision이 높아지지만 Recall이 낮아진다 (누락 증가).\n")
section_lines.append("- F1 Score가 가장 높은 conf 값이 Precision과 Recall의 최적 균형점이다.\n")
section_lines.append("- 광고 서비스 특성상 오탐보다 누락이 더 치명적이므로, F1이 최대인 구간에서 Recall 쪽으로 약간 치우친 값을 최종 선정하는 것이 유리하다.\n")

section_text = "".join(section_lines)

with open(REPORT_PATH, "a", encoding="utf-8") as f:
    f.write(section_text)

print("\n=== Project_Report.md 기록 완료 ===")
print("\n최종 결과 요약:")
print(df.to_string(index=False))

# Best F1 안내
best_row = df.loc[df["F1"].idxmax()]
print(f"\n★ Best F1 = {best_row['F1']} @ conf={best_row['conf']}")
