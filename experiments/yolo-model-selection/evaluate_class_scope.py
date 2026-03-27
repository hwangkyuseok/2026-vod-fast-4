from ultralytics import YOLO
import pandas as pd
import numpy as np
from pathlib import Path

# =========================
# 1. 기본 설정
# =========================
MODEL_PATH = "yolov8l.pt"
VIDEO_PATH = "SampleVideo_Scenes"
DATA_YAML = "coco128.yaml"
REPORT_PATH = Path("Project_Report.md")

# COCO MVP 15 class ids
MVP_CLASS_IDS = [16, 26, 39, 41, 45, 53, 56, 57, 59, 60, 62, 63, 65, 67, 72]

# =========================
# 2. 실험 목록
# =========================
experiments = [
    {
        "실험 번호": 1,
        "모델": "YOLOv8l",
        "클래스 범위": "COCO 80",
        "conf": 0.25,
        "iou": 0.7,
        "imgsz": 640,
        "classes": None,
        "총평": "baseline"
    },
    {
        "실험 번호": 2,
        "모델": "YOLOv8l",
        "클래스 범위": "MVP 15",
        "conf": 0.25,
        "iou": 0.7,
        "imgsz": 640,
        "classes": MVP_CLASS_IDS,
        "총평": "필터 적용"
    }
]

# =========================
# 3. 모델 로드
# =========================
model = YOLO(MODEL_PATH)
rows = []

# =========================
# 4. 실험 실행
# =========================
for exp in experiments:
    print(f"\n=== Experiment {exp['실험 번호']} 시작 ===")
    print(
        f"scope={exp['클래스 범위']}, conf={exp['conf']}, "
        f"iou={exp['iou']}, imgsz={exp['imgsz']}"
    )

    # COCO128 전체 이미지는 유지하고, 평가 클래스만 제한
    metrics = model.val(
        data=DATA_YAML,
        imgsz=exp["imgsz"],
        conf=exp["conf"],
        iou=exp["iou"],
        classes=exp["classes"],
        verbose=False
    )

    precision = metrics.box.p
    recall = metrics.box.r

    if hasattr(precision, "__len__"):
        precision = float(np.nanmean(precision))
    else:
        precision = float(precision)

    if hasattr(recall, "__len__"):
        recall = float(np.nanmean(recall))
    else:
        recall = float(recall)

    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    # 샘플 영상 기준 탐지 수
    scene_files = sorted(Path(VIDEO_PATH).glob("*.mp4"))

    print(f"샘플 scene clip 수: {len(scene_files)}")
    print("이제 SampleVideo_Scenes 추론 시작")

    total_detected = 0

    for idx, scene_file in enumerate(scene_files, start=1):
        print(f"[{idx}/{len(scene_files)}] {scene_file.name} 추론 중...")

        results = model.predict(
            source=str(scene_file),
            conf=exp["conf"],
            iou=exp["iou"],
            imgsz=exp["imgsz"],
            classes=exp["classes"],
            verbose=False
        )

    for result in results:
        if result.boxes is not None:
            total_detected += len(result.boxes)

    print(f"총 탐지 수: {total_detected}")

    rows.append({
        "실험 번호": exp["실험 번호"],
        "모델": exp["모델"],
        "클래스 범위": exp["클래스 범위"],
        "conf": exp["conf"],
        "iou": exp["iou"],
        "imgsz": exp["imgsz"],
        "Precision": round(precision, 4),
        "Recall": round(recall, 4),
        "F1": round(f1, 4),
        "탐지 수": total_detected,
        "오탐": "GT 필요",
        "누락": "GT 필요",
        "총평": exp["총평"]
    })

df = pd.DataFrame(rows)

# =========================
# 5. Project_Report.md에 붙일 내용 생성
# =========================
section_lines = []
section_lines.append("\n\n## 2. 객체 클래스 정의 (필터링 실험)\n")
section_lines.append("\n")
section_lines.append("광고 서비스와 직접 연관된 객체만 탐지하기 위해 COCO 80 클래스 중 MVP 15 클래스를 선정하였다.\n")
section_lines.append("이후 동일한 YOLOv8l 모델 조건에서 COCO 80 클래스와 MVP 15 클래스 범위를 비교하였다.\n")
section_lines.append("\n")
section_lines.append("### 실험 설정\n")
section_lines.append("\n")
section_lines.append("| 항목 | 설정 |\n")
section_lines.append("|---|---|\n")
section_lines.append(f"| 모델 | {MODEL_PATH} |\n")
section_lines.append(f"| 검증 데이터셋 | {DATA_YAML} |\n")
section_lines.append(f"| 샘플 영상 | {VIDEO_PATH} |\n")
section_lines.append("| 평가 방식 | COCO128 전체 이미지는 유지하고 클래스 범위만 제한 |\n")
section_lines.append("\n")
section_lines.append("### 클래스 범위 비교 결과\n")
section_lines.append("\n")
section_lines.append(df.to_markdown(index=False))
section_lines.append("\n\n")
section_lines.append("### 결과 해석\n")
section_lines.append("\n")
section_lines.append("- COCO 80은 범용 객체 탐지 baseline 결과이다.\n")
section_lines.append("- MVP 15는 광고 서비스와 직접 연결 가능한 객체만 대상으로 재평가한 결과이다.\n")
section_lines.append("- 탐지 수는 샘플 영상 기준 총 탐지 객체 수이다.\n")
section_lines.append("- 오탐/누락은 Ground Truth 상세 매칭이 필요하므로 현재는 별도 표기하였다.\n")

section_text = "".join(section_lines)

# =========================
# 6. 기존 Project_Report.md에 append
# =========================
if not REPORT_PATH.exists():
    REPORT_PATH.write_text("# Project Report\n", encoding="utf-8")

with open(REPORT_PATH, "a", encoding="utf-8") as f:
    f.write(section_text)

print("\n=== Project_Report.md 기록 완료 ===")
print(df)