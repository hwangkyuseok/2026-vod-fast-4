import os
import cv2
import torch
import torchvision
from torchvision import transforms

# 91 class mapping for PyTorch Faster R-CNN
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

# 80 class mapping for YOLO
YOLO_CLASSES = [
    'person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus', 'train', 'truck', 'boat',
    'traffic light', 'fire hydrant', 'stop sign', 'parking meter', 'bench', 'bird', 'cat',
    'dog', 'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe', 'backpack',
    'umbrella', 'handbag', 'tie', 'suitcase', 'frisbee', 'skis', 'snowboard', 'sports ball',
    'kite', 'baseball bat', 'baseball glove', 'skateboard', 'surfboard', 'tennis racket',
    'bottle', 'wine glass', 'cup', 'fork', 'knife', 'spoon', 'bowl', 'banana', 'apple',
    'sandwich', 'orange', 'broccoli', 'carrot', 'hot dog', 'pizza', 'donut', 'cake', 'chair',
    'couch', 'potted plant', 'bed', 'dining table', 'toilet', 'tv', 'laptop', 'mouse', 'remote',
    'keyboard', 'cell phone', 'microwave', 'oven', 'toaster', 'sink', 'refrigerator', 'book',
    'clock', 'vase', 'scissors', 'teddy bear', 'hair drier', 'toothbrush'
]

def calculate_iou(boxA, boxB):
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])
    interArea = max(0, xB - xA) * max(0, yB - yA)
    boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
    boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
    iou = interArea / float(boxAArea + boxBArea - interArea + 1e-6)
    return iou

def evaluate_predictions(gt_list, pred_list, iou_thresh=0.5):
    tp = 0
    fp = 0
    pred_list.sort(key=lambda x: x['conf'], reverse=True)
    matched_gt = set()
    for pred in pred_list:
        best_iou = 0
        best_gt_idx = -1
        for idx, gt in enumerate(gt_list):
            if idx in matched_gt: continue
            if gt['class'] != pred['class']: continue
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

def load_yolo_labels(label_path, img_width, img_height):
    gt_boxes = []
    if not os.path.exists(label_path):
        return gt_boxes
    
    with open(label_path, 'r') as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) == 5:
                cls_id = int(parts[0])
                if cls_id >= len(YOLO_CLASSES): continue
                x_center = float(parts[1]) * img_width
                y_center = float(parts[2]) * img_height
                width = float(parts[3]) * img_width
                height = float(parts[4]) * img_height
                
                x1 = x_center - width / 2
                y1 = y_center - height / 2
                x2 = x_center + width / 2
                y2 = y_center + height / 2
                
                cls_name = YOLO_CLASSES[cls_id]
                gt_boxes.append({"box": [x1, y1, x2, y2], "class": cls_name})
    return gt_boxes

def evaluate_cnn_on_coco128():
    dataset_dir = r"C:\Users\user\MyProject\hellovision project2\(NEW) Project2\hv-context-ad-mvp\datasets\coco128"
    images_dir = os.path.join(dataset_dir, "images", "train2017")
    labels_dir = os.path.join(dataset_dir, "labels", "train2017")
    
    if not os.path.exists(images_dir):
        print("ERROR: datasets/coco128 could not be found.")
        return
        
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

    image_files = [f for f in os.listdir(images_dir) if f.endswith(('.jpg', '.png'))]
    
    tp_total, fp_total, fn_total = 0, 0, 0
    
    print(f"Evaluating {len(image_files)} images from COCO128...")
    
    for idx, img_name in enumerate(image_files):
        img_path = os.path.join(images_dir, img_name)
        label_name = os.path.splitext(img_name)[0] + ".txt"
        label_path = os.path.join(labels_dir, label_name)
        
        frame = cv2.imread(img_path)
        if frame is None: continue
        
        h, w = frame.shape[:2]
        gt_boxes = load_yolo_labels(label_path, w, h)
        
        # Inference
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image_tensor = transform(rgb_frame).to(device)
        with torch.no_grad():
            cnn_prediction = cnn_model([image_tensor])[0]
            
        cnn_preds = []
        scores = cnn_prediction['scores']
        valid_indices = scores > 0.25
        for i in range(len(scores)):
            if valid_indices[i]:
                x1, y1, x2, y2 = cnn_prediction['boxes'][i].tolist()
                label_id = int(cnn_prediction['labels'][i])
                if label_id < len(COCO_INSTANCE_CATEGORY_NAMES):
                    cls_name = COCO_INSTANCE_CATEGORY_NAMES[label_id]
                else:
                    continue

                if cls_name == "N/A":
                    continue

                conf = float(scores[i])
                cnn_preds.append({"box": [x1, y1, x2, y2], "class": cls_name, "conf": conf})
                
        tp, fp, fn = evaluate_predictions(gt_boxes, cnn_preds, iou_thresh=0.5)
        tp_total += tp
        fp_total += fp
        fn_total += fn
        
        if (idx+1) % 10 == 0:
            print(f"Processed {idx+1}/{len(image_files)}")
            
    precision = tp_total / (tp_total + fp_total) if (tp_total + fp_total) > 0 else 0.0
    recall = tp_total / (tp_total + fn_total) if (tp_total + fn_total) > 0 else 0.0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    
    print("\n=== Faster R-CNN COCO128 Evaluation ===")
    print(f"Total Images: {len(image_files)}")
    print(f"Precision: {precision:.4f}")
    print(f"Recall:    {recall:.4f}")
    print(f"F1 Score:  {f1:.4f}")
    
    with open("cnn_coco128_metrics.txt", "w", encoding="utf-8") as f:
        f.write("=== Faster R-CNN COCO128 Evaluation Metrics ===\n")
        f.write("Score Threshold: 0.25\n")
        f.write("Match IoU Threshold: 0.5\n")
        f.write(f"Precision: {precision:.4f}\n")
        f.write(f"Recall: {recall:.4f}\n")
        f.write(f"F1 Score: {f1:.4f}\n")

if __name__ == "__main__":
    evaluate_cnn_on_coco128()
