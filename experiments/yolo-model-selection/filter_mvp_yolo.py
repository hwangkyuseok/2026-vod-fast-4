from ultralytics import YOLO
from pathlib import Path
import json

# =========================
# 1. 설정
# =========================
MODEL_PATH = "yolov8l.pt"
VIDEO_PATH = "SampleVideo.mp4"   # 지금 프로젝트 루트 기준
OUTPUT_DIR = Path("runs/mvp15_test")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# COCO class ids
COCO_MVP_CLASS_MAP = {
    16: "dog",
    26: "handbag",
    39: "bottle",
    41: "cup",
    45: "bowl",
    53: "pizza",
    56: "chair",
    57: "couch",
    59: "bed",
    60: "dining table",
    62: "tv",
    63: "laptop",
    65: "remote",
    67: "cell phone",
    72: "refrigerator"
}

MVP_CLASS_IDS = list(COCO_MVP_CLASS_MAP.keys())

CONF = 0.25
IOU = 0.7
IMGSZ = 640

# =========================
# 2. 모델 로드
# =========================
model = YOLO(MODEL_PATH)

# =========================
# 3. 추론 실행
# =========================
results = model.predict(
    source=VIDEO_PATH,
    conf=CONF,
    iou=IOU,
    imgsz=IMGSZ,
    classes=MVP_CLASS_IDS,
    save=True,
    project=str(OUTPUT_DIR),
    name="predict",
    exist_ok=True,
    stream=True
)

# =========================
# 4. 프레임별 결과 저장
# =========================
frame_summaries = []
total_detected = 0
confidence_sum = 0.0
confidence_count = 0

for frame_idx, result in enumerate(results):
    frame_data = {
        "frame_index": frame_idx,
        "detected_count": 0,
        "objects": []
    }

    names = result.names

    if result.boxes is not None:
        for box in result.boxes:
            cls_id = int(box.cls[0].item())
            cls_name = names[cls_id]
            conf = float(box.conf[0].item())
            xyxy = [round(v, 2) for v in box.xyxy[0].tolist()]

            obj = {
                "class_id": cls_id,
                "class_name": cls_name,
                "confidence": round(conf, 4),
                "bbox": xyxy
            }
            frame_data["objects"].append(obj)

            total_detected += 1
            confidence_sum += conf
            confidence_count += 1

    frame_data["detected_count"] = len(frame_data["objects"])
    frame_summaries.append(frame_data)

avg_conf = round(confidence_sum / confidence_count, 4) if confidence_count > 0 else 0.0

summary = {
    "model": MODEL_PATH,
    "video_path": VIDEO_PATH,
    "class_scope": "MVP 15",
    "mvp_classes": COCO_MVP_CLASS_MAP,
    "conf": CONF,
    "iou": IOU,
    "imgsz": IMGSZ,
    "total_frames_processed": len(frame_summaries),
    "total_detected_objects": total_detected,
    "average_confidence": avg_conf
}

with open(OUTPUT_DIR / "mvp15_frame_results.json", "w", encoding="utf-8") as f:
    json.dump(frame_summaries, f, ensure_ascii=False, indent=2)

with open(OUTPUT_DIR / "mvp15_summary.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

print("MVP 15 클래스 추론 완료")
print(f"총 프레임 수: {len(frame_summaries)}")
print(f"총 탐지 객체 수: {total_detected}")
print(f"평균 confidence: {avg_conf}")
print(f"결과 저장 폴더: {OUTPUT_DIR / 'predict'}")