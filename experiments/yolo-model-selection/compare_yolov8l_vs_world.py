"""
YOLOv8L vs YOLOv8-World 비교 실험 스크립트 (MVP 15 클래스 기준)
--------------------------------------------------
비교 조건:
  - 두 모델 모두 MVP 15 클래스 동일 조건으로 비교
  - COCO128 validation → Precision / Recall / F1
  - SampleVideo_Scenes 218씬 중간 프레임 추론 → 탐지 수 / FPS

IndexError 해결:
  - YOLOv8-World val() 시 coco80 전체로 set_classes → confusion matrix 크기 일치
  - val() 결과 필터는 classes=MVP_CLASS_IDS 로 MVP 15만 평가
  - 218씬 추론 시에는 set_classes(MVP_CLASS_NAMES) 로 재설정
"""

import cv2
import re
import sys
import time
import warnings
from pathlib import Path
from ultralytics import YOLO

warnings.filterwarnings("ignore")

# =========================================================
# 설정
# =========================================================
BASE_DIR     = Path(__file__).resolve().parent
SCENES_DIR   = BASE_DIR / "SampleVideo_Scenes"
DATA_YAML    = "coco128.yaml"
REPORT_PATH  = BASE_DIR / "Project_Report.md"
RESULT_TXT   = BASE_DIR / "compare_yolov8l_vs_world.txt"

YOLOV8L_PATH = BASE_DIR / "yolov8l.pt"
WORLD_MODEL  = "yolov8l-worldv2.pt"

# MVP 15 클래스
MVP_CLASS_IDS = [16, 26, 39, 41, 45, 53, 56, 57, 59, 60, 62, 63, 65, 67, 72]
MVP_CLASS_NAMES = [
    "dog", "handbag", "bottle", "cup", "bowl",
    "apple", "chair", "couch", "bed", "dining table",
    "tv", "laptop", "remote", "cell phone", "refrigerator"
]

CONF  = 0.25
IOU   = 0.7
IMGSZ = 640
BATCH = 16


# =========================================================
# 유틸
# =========================================================
def natural_sort_key(p: Path):
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", p.name)]


def calc_f1(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


def get_mean_pr(metrics):
    return float(metrics.box.mp), float(metrics.box.mr)


def progress_bar(done: int, total: int, extra: str = "") -> str:
    pct   = done / total * 100
    filled = int(pct // 5)
    bar   = "█" * filled + "░" * (20 - filled)
    return f"[{bar}] {done}/{total} ({pct:.0f}%)  {extra}"


def print_step(step: int, total_steps: int, title: str):
    print(f"\n{'='*60}")
    print(f"  STEP {step}/{total_steps}  |  {title}")
    print(f"{'='*60}")


# =========================================================
# 프레임 추출 (두 모델 공통 재사용)
# =========================================================
def extract_middle_frames(scene_files: list) -> list:
    total  = len(scene_files)
    frames = []
    failed = 0
    t0     = time.time()

    print(f"  총 {total}개 씬에서 중간 프레임 추출 중...")

    for i, path in enumerate(scene_files, 1):
        cap = cv2.VideoCapture(str(path))
        if cap.isOpened():
            fc = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, fc // 2))
            ret, frame = cap.read()
            cap.release()
            frames.append(frame if ret else None)
            if not ret:
                failed += 1
        else:
            cap.release()
            frames.append(None)
            failed += 1

        sys.stdout.write(
            f"\r  {progress_bar(i, total, f'실패:{failed}  경과:{time.time()-t0:.1f}s')}"
        )
        sys.stdout.flush()

    # None 제거
    frames = [f for f in frames if f is not None]
    print(f"\n  완료: {len(frames)}/{total} 프레임 추출  (실패: {failed})\n")
    return frames


# =========================================================
# 배치 추론 — 탐지 수만 카운트
# =========================================================
def run_inference(model, frames: list, model_label: str) -> tuple:
    total    = len(frames)
    detected = 0
    t_start  = time.time()

    print(f"  [{model_label}] {total}프레임 배치 추론 시작 (batch={BATCH})...")

    for b_start in range(0, total, BATCH):
        batch   = frames[b_start : b_start + BATCH]
        results = model.predict(
            source=batch,
            conf=CONF,
            iou=IOU,
            imgsz=IMGSZ,
            verbose=False,
            agnostic_nms=True,
        )
        for r in results:
            if r.boxes is not None:
                detected += len(r.boxes)

        done = min(b_start + BATCH, total)
        sys.stdout.write(
            f"\r  {progress_bar(done, total, f'탐지:{detected}  경과:{time.time()-t_start:.1f}s')}"
        )
        sys.stdout.flush()

    elapsed   = time.time() - t_start
    scene_fps = total / elapsed if elapsed > 0 else 0.0
    print(f"\n  [{model_label}] 완료 — 총 탐지:{detected}  소요:{elapsed:.1f}s  {scene_fps:.1f} scenes/s\n")
    return detected, elapsed


# =========================================================
# 메인
# =========================================================
def main():
    total_steps = 5
    t_total     = time.time()

    # ──────────────────────────────────────────────────────
    # STEP 1 : 씬 파일 수집 & 중간 프레임 추출
    # ──────────────────────────────────────────────────────
    print_step(1, total_steps, "씬 파일 수집 & 중간 프레임 추출")

    scene_files = sorted(SCENES_DIR.glob("*.mp4"), key=natural_sort_key)
    if not scene_files:
        print("[!] SampleVideo_Scenes 에 mp4 파일이 없습니다. 종료.")
        return
    print(f"  씬 파일: {len(scene_files)}개 발견")

    frames = extract_middle_frames(scene_files)

    # ──────────────────────────────────────────────────────
    # STEP 2 : YOLOv8L — COCO128 Validation (MVP 15 필터)
    # ──────────────────────────────────────────────────────
    print_step(2, total_steps, "YOLOv8L — COCO128 Validation (MVP 15 클래스)")

    print("  모델 로딩 중... yolov8l.pt")
    model_l      = YOLO(str(YOLOV8L_PATH))
    coco80_names = list(model_l.names.values())   # COCO 80 클래스명 순서 보존
    print(f"  conf={CONF}  iou={IOU}  imgsz={IMGSZ}  classes=MVP 15")

    print("  COCO128 validation 실행 중...")
    metrics_l = model_l.val(
        data=DATA_YAML,
        imgsz=IMGSZ,
        conf=CONF,
        iou=IOU,
        classes=MVP_CLASS_IDS,   # MVP 15 클래스만 평가
        verbose=False,
    )
    p_l, r_l = get_mean_pr(metrics_l)
    f1_l     = calc_f1(p_l, r_l)
    print(f"\n  ✔ YOLOv8L   Precision={p_l:.4f}  Recall={r_l:.4f}  F1={f1_l:.4f}")

    # ──────────────────────────────────────────────────────
    # STEP 3 : YOLOv8L — 218씬 추론 (MVP 15 필터)
    # ──────────────────────────────────────────────────────
    print_step(3, total_steps, "YOLOv8L — 218씬 추론 (MVP 15 클래스)")

    # MVP 15만 탐지하도록 클래스 필터 적용
    model_l.overrides["classes"] = MVP_CLASS_IDS
    det_l, time_l = run_inference(model_l, frames, "YOLOv8L")

    # ──────────────────────────────────────────────────────
    # STEP 4 : YOLOv8-World — COCO128 Validation & 218씬 추론
    # ──────────────────────────────────────────────────────
    print_step(4, total_steps, "YOLOv8-World — COCO128 Validation (MVP 15 클래스)")

    print(f"  모델 로딩 중... {WORLD_MODEL}  (없으면 자동 다운로드)")
    model_w = YOLO(WORLD_MODEL)

    # ── val(): COCO 80 전체로 set_classes → confusion matrix 크기 일치 ──
    print("  [val용] COCO 80 클래스 전체 set_classes 적용...")
    model_w.set_classes(coco80_names)
    print(f"  conf={CONF}  iou={IOU}  imgsz={IMGSZ}  classes=MVP 15 필터")

    print("  COCO128 validation 실행 중...")
    metrics_w = model_w.val(
        data=DATA_YAML,
        imgsz=IMGSZ,
        conf=CONF,
        iou=IOU,
        classes=MVP_CLASS_IDS,   # MVP 15 클래스만 평가
        verbose=False,
    )
    p_w, r_w = get_mean_pr(metrics_w)
    f1_w     = calc_f1(p_w, r_w)
    print(f"\n  ✔ YOLOv8-World  Precision={p_w:.4f}  Recall={r_w:.4f}  F1={f1_w:.4f}")

    # ── 218씬 추론: MVP 15 텍스트 프롬프트로 재설정 ──
    print(f"\n  [추론용] MVP 15 클래스명으로 set_classes 재설정...")
    model_w.set_classes(MVP_CLASS_NAMES)
    print_step(4, total_steps, "YOLOv8-World — 218씬 추론 (MVP 15 클래스)")
    det_w, time_w = run_inference(model_w, frames, "YOLOv8-World")

    # ──────────────────────────────────────────────────────
    # STEP 5 : 결과 정리 & Project_Report.md 업데이트
    # ──────────────────────────────────────────────────────
    print_step(5, total_steps, "결과 정리 & Project_Report.md 업데이트")

    fps_l        = len(frames) / time_l if time_l > 0 else 0.0
    fps_w        = len(frames) / time_w if time_w > 0 else 0.0
    winner_acc   = "YOLOv8L" if f1_l >= f1_w else "YOLOv8-World"
    winner_speed = "YOLOv8L" if fps_l >= fps_w else "YOLOv8-World"

    summary = f"""
======================================================
  YOLOv8L  vs  YOLOv8-World  비교 결과
  기준: MVP 15 클래스  |  conf={CONF}  iou={IOU}  imgsz={IMGSZ}
======================================================

[COCO128 Validation — MVP 15 클래스]
  항목              YOLOv8L        YOLOv8-World
  Precision         {p_l:.4f}         {p_w:.4f}
  Recall            {r_l:.4f}         {r_w:.4f}
  F1 Score          {f1_l:.4f}         {f1_w:.4f}

[SampleVideo {len(frames)}씬 추론 — MVP 15 클래스]
  항목              YOLOv8L        YOLOv8-World
  총 탐지 수        {det_l:<14} {det_w}
  소요 시간(s)      {time_l:<14.1f} {time_w:.1f}
  처리속도(fps)     {fps_l:<14.1f} {fps_w:.1f}

  ✔ 정확도 우위 → {winner_acc}  (F1: {max(f1_l, f1_w):.4f})
  ✔ 속도 우위   → {winner_speed}  (FPS: {max(fps_l, fps_w):.1f})
======================================================
총 실험 소요: {(time.time() - t_total):.0f}s
"""
    print(summary)

    # txt 저장
    RESULT_TXT.write_text(summary, encoding="utf-8")
    print(f"  결과 저장 → {RESULT_TXT}")

    # ── Project_Report.md 섹션 3 수치 업데이트 ──
    report = REPORT_PATH.read_text(encoding="utf-8")

    # Validation 표 채우기
    report = report.replace(
        "| **YOLOv8L** | - | - | - |\n| **YOLOv8-World** | - | - | - |",
        f"| **YOLOv8L** | {p_l:.4f} | {r_l:.4f} | {f1_l:.4f} |\n"
        f"| **YOLOv8-World** | {p_w:.4f} | {r_w:.4f} | {f1_w:.4f} |",
        1
    )
    # Inference 표 채우기
    report = report.replace(
        "| **YOLOv8L** | - | - | - |\n| **YOLOv8-World** | - | - | - |",
        f"| **YOLOv8L** | {det_l} | {time_l:.1f} | {fps_l:.1f} |\n"
        f"| **YOLOv8-World** | {det_w} | {time_w:.1f} | {fps_w:.1f} |",
        1
    )
    # 진행 중 표시 제거
    report = report.replace(
        "> ⏳ **실험 진행 중** — `compare_yolov8l_vs_world.py` 실행 후 결과 수치가 자동으로 추가됩니다.\n\n",
        ""
    )

    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"  Project_Report.md 섹션 3 업데이트 완료!")

    print(f"\n{'='*60}")
    print(f"  실험 완료!  총 소요: {(time.time() - t_total):.0f}s")
    print(f"  정확도 우위 → {winner_acc}  (F1: YOLOv8L={f1_l:.4f} / World={f1_w:.4f})")
    print(f"  속도 우위   → {winner_speed}  (FPS: YOLOv8L={fps_l:.1f} / World={fps_w:.1f})")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
