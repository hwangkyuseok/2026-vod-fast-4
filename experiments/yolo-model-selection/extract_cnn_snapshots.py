import os
import cv2
import torch
import torchvision
from torchvision import transforms

def main():
    movies_dir = "SampleVideo_Scenes"
    output_dir = "snapshots_cnn"

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    print("Loading Faster R-CNN (CNN) model...")
    # `weights=torchvision.models.detection.FasterRCNN_ResNet50_FPN_Weights.DEFAULT`
    # or `pretrained=True` for older PyTorch versions
    try:
        model = torchvision.models.detection.fasterrcnn_resnet50_fpn(pretrained=True)
    except TypeError:
        # For newer torchvision
        weights = torchvision.models.detection.FasterRCNN_ResNet50_FPN_Weights.DEFAULT
        model = torchvision.models.detection.fasterrcnn_resnet50_fpn(weights=weights)

    model.eval()

    device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
    print(f"Using device: {device}")
    model = model.to(device)

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

    video_files = [f for f in os.listdir(movies_dir) if f.endswith(".mp4")]
    video_files.sort()

    print(f"Found {len(video_files)} videos. Processing...")

    transform = transforms.Compose([transforms.ToTensor()])

    for idx, video_filename in enumerate(video_files):
        video_path = os.path.join(movies_dir, video_filename)
        
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print(f"Failed to open: {video_path}")
            continue
            
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            continue

        middle_frame_idx = total_frames // 2
        cap.set(cv2.CAP_PROP_POS_FRAMES, middle_frame_idx)
        
        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            continue

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image_tensor = transform(rgb_frame).to(device)
        
        with torch.no_grad():
            prediction = model([image_tensor])[0]
        
        annotated_frame = frame.copy()
        
        for i in range(len(prediction['boxes'])):
            score = prediction['scores'][i].item()
            if score > 0.5:
                box = prediction['boxes'][i].cpu().numpy().astype(int)
                class_id = prediction['labels'][i].item()
                
                label = f"{COCO_INSTANCE_CATEGORY_NAMES[class_id]}: {score:.2f}" if class_id < len(COCO_INSTANCE_CATEGORY_NAMES) else f"ID {class_id}: {score:.2f}"
                
                cv2.rectangle(annotated_frame, (box[0], box[1]), (box[2], box[3]), (0, 0, 255), 2)
                (label_width, label_height), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                cv2.rectangle(annotated_frame, (box[0], box[1] - label_height - baseline), (box[0] + label_width, box[1]), (0, 0, 255), cv2.FILLED)
                cv2.putText(annotated_frame, label, (box[0], box[1] - baseline), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        snapshot_filename = video_filename.replace(".mp4", ".jpg")
        snapshot_path = os.path.join(output_dir, snapshot_filename)
        cv2.imwrite(snapshot_path, annotated_frame)
        
        if (idx + 1) % 20 == 0:
            print(f"Processed {idx + 1}/{len(video_files)} videos...")

    print("Done! Snapshots saved to", output_dir)

if __name__ == "__main__":
    main()
