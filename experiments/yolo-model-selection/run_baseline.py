import warnings
warnings.filterwarnings('ignore')

from ultralytics import YOLO

def main():
    print("Loading baseline model (yolov8n.pt)...")
    model = YOLO('yolov8n.pt')
    
    # We use coco128 as a default small dataset for baseline measurement.
    # Replace 'coco128.yaml' with your custom dataset yaml path (e.g., 'custom_data.yaml') when ready.
    dataset_yaml = 'coco128.yaml'
    
    print(f"Running validation on {dataset_yaml} (Settings: imgsz=640, conf=0.25, iou=0.45)...")
    # Using the same conf and iou as used in video_analyzer.py for inference
    metrics = model.val(data=dataset_yaml, imgsz=640, conf=0.25, iou=0.45, batch=16, verbose=False)
    
    print("\n========================================")
    print("           Baseline Metrics (B0)        ")
    print("========================================")
    print(f"mAP50:      {metrics.box.map50:.4f}")
    print(f"mAP50-95:   {metrics.box.map:.4f}")
    
    mean_precision = metrics.box.mp
    mean_recall = metrics.box.mr
    print(f"Precision:  {mean_precision:.4f}")
    print(f"Recall:     {mean_recall:.4f}")
    
    if (mean_precision + mean_recall) > 0:
        f1 = 2 * (mean_precision * mean_recall) / (mean_precision + mean_recall)
        print(f"F1 Score:   {f1:.4f}")
    else:
        print("F1 Score:   0.0000")
    
    print("========================================")
    print("이 수치를 Project_Report.md 의 B0 (baseline) 행에 기록하세요.")
    print("향후 자체 데이터셋이 준비되면 dataset_yaml 변수를 수정하여 다시 측정하시면 됩니다.")

if __name__ == '__main__':
    main()
