import os
import cv2
import torch
import torchvision
from ultralytics import YOLO
from torchvision import transforms

def count_yolo(model_path, movies_dir):
    model = YOLO(model_path)
    video_files = [f for f in os.listdir(movies_dir) if f.endswith(".mp4")]
    total_detections = 0
    
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
            
        results = model(frame, verbose=False)
        # Count boxes in this frame
        total_detections += len(results[0].boxes)
        
    return total_detections

def count_cnn(movies_dir):
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
            
        # Count boxes with score > 0.5 (same logic as used for drawing boxes before)
        scores = prediction['scores']
        valid_detections = (scores > 0.5).sum().item()
        total_detections += valid_detections
        
    return total_detections

if __name__ == "__main__":
    movies_dir = "SampleVideo_Scenes"
    
    print("Counting YOLOv8n detections...")
    n_count = count_yolo("yolov8n.pt", movies_dir)
    print(f"YOLOv8n total detections: {n_count}")
    
    print("Counting YOLOv8l detections...")
    l_count = count_yolo("yolov8l.pt", movies_dir)
    print(f"YOLOv8l total detections: {l_count}")
    
    print("Counting Faster R-CNN detections...")
    cnn_count = count_cnn(movies_dir)
    print(f"Faster R-CNN total detections: {cnn_count}")
