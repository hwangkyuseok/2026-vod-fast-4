import os
import cv2
import json
import torch
import torchvision
from ultralytics import YOLO
from ultralytics.utils.metrics import box_iou
import numpy as np

# Ground Truth 흉내 (Baseline == YOLOv8l with conf=0.25, iou=0.7, imgsz=640)
# 원래는 사람이 직접 라벨링한 Ground Truth가 있어야 mAP, Precision, Recall을 정확히 구할 수 있습니다.
# 여기서는 요청하신 "Baseline을 측정하라"는 의미를
# 1) 현재 설정(conf=0.25, iou=0.7, imgsz=640)으로 YOLOv8l이 얼마나 탐지하는지 기본 메트릭을 추산하거나
# 2) 이를 기준으로 삼아 향후 파라미터 튜닝 시 비교군으로 쓰기 위해 기록을 남기는 것으로 해석했습니다.
#
# 따라서 이 스크립트는 "현재 파라미터 세팅"으로 돌렸을 때의 탐지 통계를 자세히 기록합니다.

def run_baseline_measurement(movies_dir, model_path="yolov8l.pt", output_file="baseline_results.txt"):
    print("Loading Baseline YOLOv8l model...")
    model = YOLO(model_path)
    
    video_files = [f for f in os.listdir(movies_dir) if f.endswith(".mp4")]
    video_files.sort()
    
    total_detections = 0
    class_counts = {}
    
    # 설정된 Baseline 파라미터
    conf_thresh = 0.25
    iou_thresh = 0.7
    img_size = 640
    
    print(f"Running Baseline Measurement on {len(video_files)} scenes.")
    print(f"Parameters: conf={conf_thresh}, iou={iou_thresh}, imgsz={img_size}")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("=== YOLOv8l Baseline Measurement ===\n")
        f.write(f"Parameters: conf={conf_thresh}, iou={iou_thresh}, imgsz={img_size}\n\n")
        
        for idx, video_filename in enumerate(video_files):
            video_path = os.path.join(movies_dir, video_filename)
            
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened(): continue
            
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total_frames <= 0:
                cap.release()
                continue
                
            middle_frame_idx = total_frames // 2
            cap.set(cv2.CAP_PROP_POS_FRAMES, middle_frame_idx)
            ret, frame = cap.read()
            cap.release()
            
            if not ret or frame is None: continue
                
            # Baseline 추론 적용
            results = model(frame, imgsz=img_size, conf=conf_thresh, iou=iou_thresh, verbose=False)
            boxes = results[0].boxes
            
            detections = len(boxes)
            total_detections += detections
            
            # 클래스별 집계
            for box in boxes:
                cls_id = int(box.cls[0])
                cls_name = model.names[cls_id]
                class_counts[cls_name] = class_counts.get(cls_name, 0) + 1
                
            f.write(f"[{video_filename}] - Detections: {detections}\n")
            
            if (idx+1) % 50 == 0:
                print(f"Processed {idx+1}/{len(video_files)}...")
                
        f.write("\n=== Summary ===\n")
        f.write(f"Total Detections across {len(video_files)} frames: {total_detections}\n")
        f.write("Class Breakdown:\n")
        for cls_name, count in sorted(class_counts.items(), key=lambda x: x[1], reverse=True):
            f.write(f"  - {cls_name}: {count}\n")
            
        # mAP, Precision, Recall 계산을 위한 안내 문구 기록
        f.write("\n[Notice regarding mAP, Precision, and Recall]\n")
        f.write("실제 mAP, Precision, Recall 밎 누락/오검출 객체 수를 정확히 구하려면\n")
        f.write("사람이 직접 어노테이션(라벨링)한 정답지(Ground Truth) 데이터셋이 필요합니다.\n")
        f.write("현재는 정답지가 없으므로, 이 측정값을 '의사 정답지(Pseudo Ground Truth Baseline)'로 삼아\n")
        f.write("향후 튜닝된 파라미터가 이 기준 대비 얼마나 검출량을 늘렸는지(Recall 향상 목적) 또는\n")
        f.write("얼마나 줄였는지(Precision 향상 목적)를 비교하는 상대 지표로 활용할 수 있습니다.\n")

    print(f"Baseline measurement complete. Results saved to {output_file}")
    
    # 요약 출력
    print(f"\nTotal Detections: {total_detections}")
    print("Top 5 Detected Classes:")
    top_5 = sorted(class_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    for k, v in top_5:
        print(f"  {k}: {v}")

if __name__ == "__main__":
    run_baseline_measurement("SampleVideo_Scenes", "yolov8l.pt", "baseline_yolov8l_results.txt")
