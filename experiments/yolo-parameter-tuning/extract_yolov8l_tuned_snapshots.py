import os
import cv2
from ultralytics import YOLO

def main():
    movies_dir = "SampleVideo_Scenes"
    output_dir = "snapshots_yolov8l_reviseParameter"
    model_path = "yolov8l.pt"

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Load YOLO model
    print("Loading YOLOv8l model...")
    model = YOLO(model_path)

    # Get all mp4 files
    video_files = [f for f in os.listdir(movies_dir) if f.endswith(".mp4")]
    video_files.sort()

    print(f"Found {len(video_files)} videos. Processing with tuned parameters (conf=0.5, iou=0.5, imgsz=736)...")

    for idx, video_filename in enumerate(video_files):
        video_path = os.path.join(movies_dir, video_filename)
        
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print(f"Failed to open video: {video_path}")
            continue
            
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            print(f"Video {video_path} has 0 frames.")
            cap.release()
            continue

        # Get the middle frame
        middle_frame_idx = total_frames // 2
        cap.set(cv2.CAP_PROP_POS_FRAMES, middle_frame_idx)
        
        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            print(f"Failed to read frame at index {middle_frame_idx} from {video_path}")
            continue

        # Run YOLO detection with tuned parameters
        results = model(frame, imgsz=736, conf=0.5, iou=0.5, verbose=False)
        
        # Get annotated image (We only save if there is at least one object detected with these strict parameters, 
        # or we could save all. I'll save all to match the previous logic of saving a snapshot per scene)
        annotated_frame = results[0].plot()

        # Save snapshot
        snapshot_filename = video_filename.replace(".mp4", ".jpg")
        snapshot_path = os.path.join(output_dir, snapshot_filename)
        
        cv2.imwrite(snapshot_path, annotated_frame)
        
        if (idx + 1) % 20 == 0:
            print(f"Processed {idx + 1}/{len(video_files)} videos...")

    print("Done! Snapshots saved to", output_dir)

if __name__ == "__main__":
    main()
