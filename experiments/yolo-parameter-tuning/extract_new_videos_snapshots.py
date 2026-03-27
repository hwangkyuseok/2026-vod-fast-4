import os
import cv2
from ultralytics import YOLO

# ── 튜닝 파라미터
CONF          = 0.25
IOU           = 0.70
IMGSZ         = 800
MODEL_PATH    = "yolov8l.pt"
MVP_CLASS_IDS = [16, 26, 39, 41, 45, 53, 56, 57, 59, 60, 62, 63, 65, 67, 72]

# ── 영상 세트 (입력 폴더 → 출력 폴더)
VIDEO_SETS = {
    "요리":   ("SampleVideo_Scenes2", "snapshots_scenes2"),
    "자취방": ("SampleVideo_Scenes3", "snapshots_scenes3"),
}

def process_video_set(model, name, input_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    video_files = sorted([f for f in os.listdir(input_dir) if f.endswith(".mp4")])
    total = len(video_files)
    print(f"\n[{name}] {total}개 클립 처리 시작 → {output_dir}")

    saved, no_det = 0, 0

    for idx, video_filename in enumerate(video_files):
        video_path = os.path.join(input_dir, video_filename)

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print(f"  열기 실패: {video_filename}")
            continue

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            continue

        # 중간 프레임 추출
        cap.set(cv2.CAP_PROP_POS_FRAMES, total_frames // 2)
        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            continue

        # YOLO 추론 (MVP 15 클래스만)
        results = model(
            frame,
            conf=CONF,
            iou=IOU,
            imgsz=IMGSZ,
            classes=MVP_CLASS_IDS,
            verbose=False
        )

        det_count = len(results[0].boxes)
        if det_count == 0:
            no_det += 1

        # 바운딩박스 그린 이미지 저장
        annotated = results[0].plot()
        out_path = os.path.join(output_dir, video_filename.replace(".mp4", ".jpg"))
        cv2.imwrite(out_path, annotated)
        saved += 1

        if (idx + 1) % 30 == 0 or (idx + 1) == total:
            print(f"  {idx + 1}/{total} 완료...")

    print(f"  저장: {saved}개 | 탐지 없음: {no_det}개")
    return saved, no_det


def main():
    print("=" * 55)
    print("  YOLOv8L 튜닝 스냅샷 추출")
    print(f"  conf={CONF}, iou={IOU}, imgsz={IMGSZ}")
    print("=" * 55)

    print("\nYOLOv8L 모델 로딩 중...")
    model = YOLO(MODEL_PATH)
    print("모델 로드 완료!")

    results_summary = []
    for name, (input_dir, output_dir) in VIDEO_SETS.items():
        saved, no_det = process_video_set(model, name, input_dir, output_dir)
        results_summary.append((name, input_dir, output_dir, saved, no_det))

    print("\n" + "=" * 55)
    print("  최종 요약")
    print("=" * 55)
    for name, _, output_dir, saved, no_det in results_summary:
        print(f"  [{name}] {saved}개 저장 (탐지 없음: {no_det}개) → {output_dir}/")
    print("완료!")


if __name__ == "__main__":
    main()
