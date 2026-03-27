import warnings
warnings.filterwarnings('ignore')

from ultralytics import YOLO

def evaluate_yolo(model_name, dataset_yaml='coco128.yaml', conf_thresh=0.25, iou_thresh=0.7, img_size=640):
    print(f"\nLoading baseline model ({model_name})...")
    model = YOLO(model_name)
    
    print(f"Running validation on {dataset_yaml} (Settings: imgsz={img_size}, conf={conf_thresh}, iou={iou_thresh})...")
    # Using the same conf and iou as used in record_all_baselines.py / run_baseline_yolov8l.py
    metrics = model.val(data=dataset_yaml, imgsz=img_size, conf=conf_thresh, iou=iou_thresh, batch=16, verbose=False)
    
    mean_precision = metrics.box.mp
    mean_recall = metrics.box.mr
    
    if (mean_precision + mean_recall) > 0:
        f1 = 2 * (mean_precision * mean_recall) / (mean_precision + mean_recall)
    else:
        f1 = 0.0
        
    print("\n" + "="*40)
    print(f"      Baseline Metrics: {model_name}")
    print("="*40)
    print(f"Precision:  {mean_precision:.4f}")
    print(f"Recall:     {mean_recall:.4f}")
    print(f"F1 Score:   {f1:.4f}")
    print("="*40)
    
    return mean_precision, mean_recall, f1

def main():
    print("Starting COCO128 Evaluation for YOLO Baseline...")
    print("Evaluating YOLOv8n (conf=0.25, iou=0.7, imgsz=640)")
    p_n, r_n, f1_n = evaluate_yolo('yolov8n.pt')
    
    print("\nEvaluating YOLOv8l (conf=0.25, iou=0.7, imgsz=640)")
    p_l, r_l, f1_l = evaluate_yolo('yolov8l.pt')
    
    # We will skip Faster R-CNN for now because ultralytics .val() handles the COCO format automatically for YOLO, 
    # but Faster R-CNN on PyTorch requires a custom dataloader for COCO128.
    # The user mainly needs the base YOLO architectures as baseline before fine-tuning.
    
    with open("coco128_metrics.txt", "w", encoding="utf-8") as f:
        f.write("=== COCO128 (Ground Truth) Baseline Metrics ===\n\n")
        f.write(f"Model: YOLOv8n\nPrecision: {p_n:.4f}\nRecall: {r_n:.4f}\nF1: {f1_n:.4f}\n\n")
        f.write(f"Model: YOLOv8l\nPrecision: {p_l:.4f}\nRecall: {r_l:.4f}\nF1: {f1_l:.4f}\n")
        
    print("\nAll done. Results saved to coco128_metrics.txt")

if __name__ == '__main__':
    main()
