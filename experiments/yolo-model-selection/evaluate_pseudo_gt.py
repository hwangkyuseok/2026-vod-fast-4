import os
import cv2
import torch
import torchvision
from torchvision import transforms
from ultralytics import YOLO

COCO_INSTANCE_CATEGORY_NAMES = [
    '__background__', 'person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus',
    'train', 'truck', 'boat', 'traffic light', 'fire hydrant', 'N/A', 'stop sign',
    'parking meter', 'bench', 'bird', 'cat', 'dog', 'horse', 'sheep', 'cow',
    'elephant', 'bear', 'zebra', 'giraffe', 'N/A', 'backpack', 'umbrella', 'N/A', 'N/A',
    'handbag', 'tie', 'suitcase', 'frisbee', 'skis', 'snowboard', 'sports ball',
    'kite', 'baseball bat', 'baseball glove', 'skateboard', 'surfboard', 'tennis racket',
    'bottle', 'N/A', 'wine glass', 'cup', 'fork', 'knife', 'spoon', 'bowl',
    'banana', 'apple', 'sandwich', 'orange', 'broccoli', 'carrot', 'hot dog', 'pizza',
    'donut', 'cake', 'chair', 'couch', 'potted plant', 'bed', 'N/A', 'dining table',
    'N/A', 'N/A', 'toilet', 'N/A', 'tv', 'laptop', 'mouse', 'remote', 'keyboard', 'cell phone',
    'microwave', 'oven', 'toaster', 'sink', 'refrigerator', 'N/A', 'book',
    'clock', 'vase', 'scissors', 'teddy bear', 'hair drier', 'toothbrush'
]

def calculate_iou(boxA, boxB):
    # Determine the (x, y)-coordinates of the intersection rectangle
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])

    # Compute the area of intersection rectangle
    interArea = max(0, xB - xA) * max(0, yB - yA)

    # Compute the area of both the prediction and ground-truth rectangles
    boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
    boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])

    # Compute the intersection over union
    iou = interArea / float(boxAArea + boxBArea - interArea + 1e-6)
    return iou

def evaluate_predictions(gt_list, pred_list, iou_thresh=0.5):
    tp = 0
    fp = 0
    
    # Sort predictions by confidence descending
    pred_list.sort(key=lambda x: x['conf'], reverse=True)
    
    matched_gt = set()
    
    for pred in pred_list:
        best_iou = 0
        best_gt_idx = -1
        
        for idx, gt in enumerate(gt_list):
            if idx in matched_gt:
                continue
            if gt['class'] != pred['class']: # Class must match
                continue
            
            iou = calculate_iou(pred['box'], list(gt['box']))
            if iou > best_iou:
                best_iou = iou
                best_gt_idx = idx
                
        if best_iou >= iou_thresh:
            tp += 1
            matched_gt.add(best_gt_idx)
        else:
            fp += 1
            
    fn = len(gt_list) - len(matched_gt)
    return tp, fp, fn

def run_evaluation(movies_dir):
    print("Loading Baseline GT model (YOLOv8l)...")
    gt_model = YOLO("yolov8l.pt")
    
    print("Loading YOLOv8n model...")
    v8n_model = YOLO("yolov8n.pt")
    
    print("Loading Faster R-CNN model...")
    try:
        cnn_model = torchvision.models.detection.fasterrcnn_resnet50_fpn(pretrained=True)
    except TypeError:
        weights = torchvision.models.detection.FasterRCNN_ResNet50_FPN_Weights.DEFAULT
        cnn_model = torchvision.models.detection.fasterrcnn_resnet50_fpn(weights=weights)
        
    device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
    cnn_model.eval()
    cnn_model = cnn_model.to(device)
    transform = transforms.Compose([transforms.ToTensor()])

    video_files = [f for f in os.listdir(movies_dir) if f.endswith(".mp4")]
    video_files.sort()
    
    metrics = {
        "YOLOv8n": {"tp": 0, "fp": 0, "fn": 0},
        "Faster R-CNN": {"tp": 0, "fp": 0, "fn": 0},
        "YOLOv8l": {"tp": 0, "fp": 0, "fn": 0} # Should be perfection since it's the GT
    }
    
    total_gt = 0
    
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
            
        # 1. Get Pseudo GT (YOLOv8l with conf=0.25)
        # We don't restrict imgsz here because baseline was run without imgsz param in record_all_baselines.py
        # Wait, run_baseline_yolov8l.py used imgsz=640.
        # So we should use imgsz=640 for consistency with the report table!
        img_size = 640
        conf_thresh = 0.25
        
        gt_results = gt_model(frame, imgsz=img_size, conf=conf_thresh, iou=0.7, verbose=False)[0]
        gt_boxes = []
        for box in gt_results.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            cls_name = gt_model.names[int(box.cls[0])]
            gt_boxes.append({"box": [x1, y1, x2, y2], "class": cls_name})
            
        total_gt += len(gt_boxes)

        # 2. Get YOLOv8n Predictions
        v8n_results = v8n_model(frame, imgsz=img_size, conf=conf_thresh, iou=0.7, verbose=False)[0]
        v8n_preds = []
        for box in v8n_results.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            cls_name = v8n_model.names[int(box.cls[0])]
            conf = float(box.conf[0])
            v8n_preds.append({"box": [x1, y1, x2, y2], "class": cls_name, "conf": conf})
            
        tp, fp, fn = evaluate_predictions(gt_boxes, v8n_preds, iou_thresh=0.5)
        metrics["YOLOv8n"]["tp"] += tp
        metrics["YOLOv8n"]["fp"] += fp
        metrics["YOLOv8n"]["fn"] += fn

        # 3. Get Faster R-CNN Predictions
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image_tensor = transform(rgb_frame).to(device)
        with torch.no_grad():
            cnn_prediction = cnn_model([image_tensor])[0]
            
        cnn_preds = []
        scores = cnn_prediction['scores']
        valid_indices = scores > 0.5
        for i in range(len(scores)):
            if valid_indices[i]:
                x1, y1, x2, y2 = cnn_prediction['boxes'][i].tolist()
                label_id = int(cnn_prediction['labels'][i])
                if label_id < len(COCO_INSTANCE_CATEGORY_NAMES):
                    cls_name = COCO_INSTANCE_CATEGORY_NAMES[label_id]
                else:
                    cls_name = "N/A"
                    
                # Fix naming differences (e.g. FasterRCNN "tv" vs YOLO "tv", etc. usually they are the same in COCO)
                conf = float(scores[i])
                cnn_preds.append({"box": [x1, y1, x2, y2], "class": cls_name, "conf": conf})

        tp, fp, fn = evaluate_predictions(gt_boxes, cnn_preds, iou_thresh=0.5)
        metrics["Faster R-CNN"]["tp"] += tp
        metrics["Faster R-CNN"]["fp"] += fp
        metrics["Faster R-CNN"]["fn"] += fn
        
        # 4. YOLOv8l vs itself (should be perfect)
        v8l_preds = []
        for box in gt_results.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            cls_name = gt_model.names[int(box.cls[0])]
            conf = float(box.conf[0])
            v8l_preds.append({"box": [x1, y1, x2, y2], "class": cls_name, "conf": conf})
            
        tp, fp, fn = evaluate_predictions(gt_boxes, v8l_preds, iou_thresh=0.5)
        metrics["YOLOv8l"]["tp"] += tp
        metrics["YOLOv8l"]["fp"] += fp
        metrics["YOLOv8l"]["fn"] += fn
        
        if (idx+1) % 50 == 0:
            print(f"Processed {idx+1}/{len(video_files)} scenes...")

    print(f"\nEvaluation Complete! Total pseudo-GT objects: {total_gt}")
    print(f"{'Model':<15} | {'Precision':<10} | {'Recall':<10} | {'F1 Score':<10}")
    print("-" * 55)
    
    with open("pseudo_gt_metrics.txt", "w", encoding="utf-8") as f:
        f.write("=== Pseudo-GT Evaluation Metrics ===\n")
        f.write(f"Total Reference (YOLOv8l) Objects: {total_gt}\n\n")
        f.write(f"{'Model':<15} | {'Precision':<10} | {'Recall':<10} | {'F1 Score':<10}\n")
        f.write("-" * 55 + "\n")
        
        for model_name, data in metrics.items():
            tp = data["tp"]
            fp = data["fp"]
            fn = data["fn"]
            
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
            
            line = f"{model_name:<15} | {precision:<10.4f} | {recall:<10.4f} | {f1:<10.4f}"
            print(line)
            f.write(line + "\n")
            
if __name__ == "__main__":
    run_evaluation("SampleVideo_Scenes")
