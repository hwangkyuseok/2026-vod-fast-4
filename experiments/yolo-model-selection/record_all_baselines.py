import os
import cv2
import json
import torch
import torchvision
from ultralytics import YOLO
from torchvision import transforms

def count_yolo_baseline(model_path, movies_dir, results_dict):
    model = YOLO(model_path)
    video_files = [f for f in os.listdir(movies_dir) if f.endswith(".mp4")]
    total_detections = 0
    class_counts = {}
    
    print(f"Counting {model_path} detections with baseline params (conf=0.25, imgsz=none)...")
    
    for video_filename in video_files:
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
            
        # YOLO baseline defaults (as used in the original count_detections.py)
        # Note: calling model(frame) without imgsz uses default (usually 640 internally, 
        # but in previous tests, maybe conf was just 0.25. Let's strictly use conf=0.25 and no imgsz)
        results = model(frame, conf=0.25, verbose=False)
        boxes = results[0].boxes
        total_detections += len(boxes)
        
        for box in boxes:
            cls_id = int(box.cls[0])
            cls_name = model.names[cls_id]
            class_counts[cls_name] = class_counts.get(cls_name, 0) + 1
            
    results_dict[model_path] = {
        "total": total_detections,
        "classes": class_counts
    }
    print(f"{model_path} -> {total_detections} detections")

def count_cnn_baseline(movies_dir, results_dict):
    try:
        model = torchvision.models.detection.fasterrcnn_resnet50_fpn(pretrained=True)
    except TypeError:
        weights = torchvision.models.detection.FasterRCNN_ResNet50_FPN_Weights.DEFAULT
        model = torchvision.models.detection.fasterrcnn_resnet50_fpn(weights=weights)
        
    model.eval()
    device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
    model = model.to(device)
    transform = transforms.Compose([transforms.ToTensor()])
    
    video_files = [f for f in os.listdir(movies_dir) if f.endswith(".mp4")]
    total_detections = 0
    
    print("Counting Faster R-CNN baseline (score > 0.5)...")
    
    for video_filename in video_files:
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
            
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image_tensor = transform(rgb_frame).to(device)
        
        with torch.no_grad():
            prediction = model([image_tensor])[0]
            
        scores = prediction['scores']
        valid_detections = (scores > 0.5).sum().item()
        total_detections += valid_detections
        
    results_dict["faster_rcnn"] = {
        "total": total_detections,
        "classes": "N/A (CNN didn't track classes in previous script)"
    }
    print(f"Faster R-CNN -> {total_detections} detections")

if __name__ == "__main__":
    movies_dir = "SampleVideo_Scenes"
    output_file = "all_models_baseline_report.txt"
    
    results = {}
    
    count_yolo_baseline("yolov8n.pt", movies_dir, results)
    count_yolo_baseline("yolov8l.pt", movies_dir, results)
    count_cnn_baseline(movies_dir, results)
    
    # Save the report
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("=== All Models Baseline Report ===\n\n")
        
        # YOLOv8n
        f.write("1. YOLOv8n Baseline (conf=0.25)\n")
        f.write(f"Total Detections: {results['yolov8n.pt']['total']}\n")
        f.write("Top 5 Classes:\n")
        top_n = sorted(results['yolov8n.pt']['classes'].items(), key=lambda x: x[1], reverse=True)[:5]
        for k, v in top_n: f.write(f"  - {k}: {v}\n")
        f.write("\n")
        
        # YOLOv8l
        f.write("2. YOLOv8l Baseline (conf=0.25)\n")
        f.write(f"Total Detections: {results['yolov8l.pt']['total']}\n")
        f.write("Top 5 Classes:\n")
        top_l = sorted(results['yolov8l.pt']['classes'].items(), key=lambda x: x[1], reverse=True)[:5]
        for k, v in top_l: f.write(f"  - {k}: {v}\n")
        f.write("\n")
        
        # CNN
        f.write("3. Faster R-CNN Baseline (score > 0.5)\n")
        f.write(f"Total Detections: {results['faster_rcnn']['total']}\n")
        f.write("\n")
        
    print(f"\nAll baselines successfully recorded to {output_file}")
