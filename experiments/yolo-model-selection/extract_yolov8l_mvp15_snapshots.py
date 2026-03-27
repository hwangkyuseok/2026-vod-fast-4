import cv2
from ultralytics import YOLO
from pathlib import Path
import json
import re

# =========================
# 1. 경로 설정
# =========================
BASE_DIR = Path(__file__).resolve().parent
SCENES_DIR = BASE_DIR / "SampleVideo_Scenes"
OUTPUT_DIR = BASE_DIR / "snapshots_yolov8l_mvp15"
SUMMARY_PATH = OUTPUT_DIR / "snapshot_summary.json"

MODEL_PATH = BASE_DIR / "yolov8l.pt"

# =========================
# 2. MVP 15 클래스 정의
# =========================
# COCO 기준 class ids
MVP_CLASS_IDS = [16, 26, 39, 41, 45, 53, 56, 57, 59, 60, 62, 63, 65, 67, 72]

# 참고용 class name 매핑
COCO_CLASS_NAMES = {
    16: "dog",
    26: "handbag",
    39: "bottle",
    41: "cup",
    45: "bowl",
    53: "apple",
    56: "chair",
    57: "couch",
    59: "bed",
    60: "dining table",
    62: "tv",
    63: "laptop",
    65: "remote",
    67: "cell phone",
    72: "refrigerator",
}

# =========================
# 3. 유틸 함수
# =========================
def natural_sort_key(path: Path):
    """
    파일명을 숫자 기준으로 자연 정렬하기 위한 키
    예: scene-2, scene-10 순서 문제 방지
    """
    return [
        int(text) if text.isdigit() else text.lower()
        for text in re.split(r"(\d+)", path.name)
    ]


def get_middle_frame(video_path: Path):
    """
    영상의 중간 프레임 1장을 읽어온다.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None, None, None

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if frame_count <= 0:
        cap.release()
        return None, None, None

    middle_idx = frame_count // 2
    cap.set(cv2.CAP_PROP_POS_FRAMES, middle_idx)

    success, frame = cap.read()
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()

    if not success:
        return None, None, None

    timestamp_sec = middle_idx / fps if fps and fps > 0 else None
    return frame, middle_idx, timestamp_sec


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


# =========================
# 4. 메인 로직
# =========================
def main():
    ensure_dir(OUTPUT_DIR)

    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"모델 파일이 없습니다: {MODEL_PATH}")

    if not SCENES_DIR.exists():
        raise FileNotFoundError(f"씬 폴더가 없습니다: {SCENES_DIR}")

    model = YOLO(str(MODEL_PATH))

    scene_files = sorted(SCENES_DIR.glob("*.mp4"), key=natural_sort_key)

    if not scene_files:
        print("mp4 씬 파일이 없습니다.")
        return

    results_summary = []
    saved_count = 0
    no_detection_count = 0
    read_fail_count = 0

    print(f"총 scene 파일 수: {len(scene_files)}")
    print("YOLOv8L + MVP15 스냅샷 추출 시작...\n")

    for idx, scene_path in enumerate(scene_files, start=1):
        frame, frame_idx, timestamp_sec = get_middle_frame(scene_path)

        if frame is None:
            print(f"[{idx}/{len(scene_files)}] 프레임 읽기 실패: {scene_path.name}")
            read_fail_count += 1
            results_summary.append({
                "scene_file": scene_path.name,
                "status": "frame_read_failed"
            })
            continue

        # YOLO 추론
        result = model.predict(
            source=frame,
            conf=0.25,
            iou=0.7,
            classes=MVP_CLASS_IDS,
            verbose=False
        )[0]

        boxes = result.boxes
        detections = []

        if boxes is not None and len(boxes) > 0:
            for box in boxes:
                cls_id = int(box.cls[0].item())
                conf = float(box.conf[0].item())
                xyxy = box.xyxy[0].tolist()

                detections.append({
                    "class_id": cls_id,
                    "class_name": COCO_CLASS_NAMES.get(cls_id, str(cls_id)),
                    "confidence": round(conf, 4),
                    "bbox_xyxy": [round(v, 2) for v in xyxy]
                })

            annotated = result.plot()

            output_name = scene_path.stem + "_mvp15.jpg"
            output_path = OUTPUT_DIR / output_name
            cv2.imwrite(str(output_path), annotated)

            saved_count += 1
            status = "saved"
            print(
                f"[{idx}/{len(scene_files)}] 저장 완료: {output_name} "
                f"| detections={len(detections)}"
            )
        else:
            status = "no_detection"
            no_detection_count += 1
            print(f"[{idx}/{len(scene_files)}] 탐지 없음: {scene_path.name}")

        results_summary.append({
            "scene_file": scene_path.name,
            "frame_index": frame_idx,
            "timestamp_sec": round(timestamp_sec, 3) if timestamp_sec is not None else None,
            "status": status,
            "num_detections": len(detections),
            "detections": detections
        })

    # summary 저장
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "model": "yolov8l.pt",
            "classes": MVP_CLASS_IDS,
            "class_names": [COCO_CLASS_NAMES[c] for c in MVP_CLASS_IDS],
            "scene_dir": str(SCENES_DIR),
            "output_dir": str(OUTPUT_DIR),
            "total_scenes": len(scene_files),
            "saved_snapshots": saved_count,
            "no_detection_scenes": no_detection_count,
            "frame_read_failed": read_fail_count,
            "results": results_summary
        }, f, ensure_ascii=False, indent=2)

    print("\n==============================")
    print("스냅샷 추출 완료")
    print(f"총 scene 수          : {len(scene_files)}")
    print(f"저장된 snapshot 수   : {saved_count}")
    print(f"탐지 없음 scene 수   : {no_detection_count}")
    print(f"프레임 읽기 실패 수  : {read_fail_count}")
    print(f"저장 폴더            : {OUTPUT_DIR}")
    print(f"요약 json            : {SUMMARY_PATH}")
    print("==============================")


if __name__ == "__main__":
    main()