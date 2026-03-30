"""
vision_yolo.py - YOLOv8l Object Detection & Safe-Area Computation
------------------------------------------------------------------
Faster R-CNN(vision_rcnn.py) 의 drop-in 대체 모듈.
analyse_frames() 시그니처/반환값이 vision_rcnn 과 완전히 동일하므로
consumer.py 에서 import 한 줄만 바꿔서 전환 가능.

For each frame:
  1. YOLOv8l (COCO-pretrained) 로 bounding box 추출
  2. object_density  = sum(bbox areas) / frame area  (capped at 1.0)
  3. safe_area       = largest empty rectangle (histogram/stack algorithm)
  4. scene cut       = inter-frame mean pixel difference > threshold

Streaming / batch mode
----------------------
on_batch callback 을 사용하면 batch_size 프레임마다 결과를 플러시하여
장시간(3h+) 영상에서도 RAM 사용량을 제한한다.

  - on_batch=None  (default): 모든 결과를 누적해 리스트로 반환
  - on_batch=<fn>:            batch_size 마다 fn(batch) 호출; [] 반환

Returns per-frame dicts ready to INSERT into analysis_vision_context.
"""

import logging
import re
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from common.config import (
    FRAME_EXTRACTION_FPS,
    SCENE_CUT_THRESHOLD,
    OPENCV_FRAME_INTERVAL,
    YOLO_CONFIDENCE_THRESHOLD,
    YOLO_BATCH_SIZE,
    YOLO_MODEL,
    YOLO_IMGSZ,
    YOLO_CLASS_IDS,
)

logger = logging.getLogger(__name__)

# ─── model singleton (loaded on first use) ─────────────────────────────────

_model = None
_device_name = None


def _get_model():
    """YOLOv8l 모델을 싱글톤으로 로드한다. 첫 호출 시 weights 자동 다운로드."""
    global _model, _device_name
    if _model is None:
        import torch
        from ultralytics import YOLO

        _device_name = "cuda:0" if torch.cuda.is_available() else "cpu"
        logger.info("Loading YOLOv8 (%s) on %s ...", YOLO_MODEL, _device_name)
        _model = YOLO(YOLO_MODEL)
        # verbose=False 로 ultralytics 내부 로그 억제
        _model.overrides["verbose"] = False
        logger.info("YOLOv8 (%s) loaded on %s", YOLO_MODEL, _device_name)
    return _model, _device_name


# ─── safe-area: largest empty rectangle via histogram ─────────────────────

def _largest_safe_rectangle(occupied_mask: np.ndarray) -> tuple[int, int, int, int]:
    """
    Boolean 점유 마스크(H×W, True=점유)에서 최대 비점유 사각형을 찾는다.
    히스토그램/스택 알고리즘 사용 (O(H*W) 시간).

    Returns (x, y, w, h) in pixel coordinates.
    """
    h, w = occupied_mask.shape
    heights = np.zeros(w, dtype=int)
    best = (0, 0, 1, 1)
    best_area = 0

    for row in range(h):
        heights = np.where(occupied_mask[row], 0, heights + 1)

        stack: list[tuple[int, int]] = []
        for col in range(w + 1):
            h_cur = heights[col] if col < w else 0
            start = col
            while stack and stack[-1][1] > h_cur:
                sc, sh = stack.pop()
                area = (col - sc) * sh
                if area > best_area:
                    best_area = area
                    best = (sc, row - sh + 1, col - sc, sh)
                start = sc
            stack.append((start, h_cur))

    return best


def _compute_safe_area(
    frame_shape: tuple,
    boxes: np.ndarray,
    person_boxes: np.ndarray | None = None,
) -> dict:
    """
    Args:
        frame_shape:  (H, W[, C])
        boxes:        np.ndarray shape (N, 4)  [x1, y1, x2, y2]  — all detected objects
        person_boxes: np.ndarray shape (M, 4) — person class boxes only (for face exclusion)

    Returns dict: safe_area_x, safe_area_y, safe_area_w, safe_area_h,
                  object_density

    Safe-area exclusion layers applied (in order):
      1. All detected-object bboxes (same as before)
      2. Top 12 % of frame  → title / logo / network bug zone
      3. Bottom 8 % of frame → subtitle / caption zone
      4. Top 50 % of each person bbox (face region) with 3 % padding
    object_density is computed from bbox areas only (layers 2-4 not counted).
    """
    h, w = frame_shape[:2]

    # ── density mask: tracks actual object area only ──────────────────────
    density_mask = np.zeros((h, w), dtype=bool)
    total_bbox_area = 0
    for box in boxes:
        x1, y1, x2, y2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        density_mask[y1:y2, x1:x2] = True
        total_bbox_area += (x2 - x1) * (y2 - y1)

    object_density = min(1.0, total_bbox_area / (h * w))

    # ── safe mask: includes extra exclusion zones for ad placement ─────────
    safe_mask = density_mask.copy()

    # Layer 2: title / logo zone — top 12 % of frame
    title_h = max(1, int(h * 0.12))
    safe_mask[:title_h, :] = True

    # Layer 3: subtitle / caption zone — bottom 8 % of frame
    caption_start = min(h - 1, int(h * 0.92))
    safe_mask[caption_start:, :] = True

    # Layer 4: face protection — top 50 % of each person bbox + 3 % padding
    if person_boxes is not None and len(person_boxes) > 0:
        for box in person_boxes:
            bx1, by1, bx2, by2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
            bw = bx2 - bx1
            bh = by2 - by1
            if bw <= 0 or bh <= 0:
                continue
            pad = max(2, int(max(bw, bh) * 0.03))   # 3 % of bbox size
            face_y2 = by1 + int(bh * 0.50)          # top 50 % = face region
            fx1 = max(0, bx1 - pad)
            fy1 = max(0, by1 - pad)
            fx2 = min(w, bx2 + pad)
            fy2 = min(h, face_y2 + pad)
            safe_mask[fy1:fy2, fx1:fx2] = True

    # ── largest-rectangle search on safe_mask ─────────────────────────────
    scale = max(1, max(h, w) // 200)
    small_mask = safe_mask[::scale, ::scale]
    sx, sy, sw, sh = _largest_safe_rectangle(small_mask)

    safe_x = int(sx) * scale
    safe_y = int(sy) * scale
    safe_w = min(int(sw) * scale, w - safe_x)
    safe_h = min(int(sh) * scale, h - safe_y)

    return {
        "safe_area_x":    safe_x,
        "safe_area_y":    safe_y,
        "safe_area_w":    safe_w,
        "safe_area_h":    safe_h,
        "object_density": float(round(object_density, 4)),
    }


# ─── scene cut detection ───────────────────────────────────────────────────

def _is_scene_cut(prev_gray: np.ndarray | None, curr_gray: np.ndarray) -> bool:
    if prev_gray is None:
        return False
    return float(cv2.absdiff(curr_gray, prev_gray).mean()) > SCENE_CUT_THRESHOLD


# ─── public API ────────────────────────────────────────────────────────────

def analyse_frames(
    frame_paths: list[str],
    on_batch: Callable[[list[dict]], None] | None = None,
    batch_size: int = YOLO_BATCH_SIZE,
) -> list[dict]:
    """
    YOLOv8l 로 프레임 리스트를 분석한다.

    Parameters
    ----------
    frame_paths : list[str]
        정렬된 프레임 JPEG 절대 경로 목록.
    on_batch : Callable[[list[dict]], None] | None
        batch_size 마다 호출되는 콜백. 제공 시 결과를 메모리에 누적하지 않고
        [] 를 반환한다. 장시간 영상에서 메모리 사용량 제한에 사용.
    batch_size : int
        on_batch 플러시 단위. 기본값 = YOLO_BATCH_SIZE (config).

    Returns
    -------
    list[dict]
        frame_index, timestamp_sec,
        safe_area_x, safe_area_y, safe_area_w, safe_area_h,
        object_density, is_scene_cut
        (on_batch 제공 시 [] 반환)
    """
    model, device_name = _get_model()
    results: list[dict] = []
    batch: list[dict] = []
    prev_gray = None

    sorted_paths = sorted(frame_paths)
    total = len(sorted_paths)

    for list_idx, fpath in enumerate(sorted_paths):
        # OPENCV_FRAME_INTERVAL: N프레임마다 1프레임 처리
        if list_idx % OPENCV_FRAME_INTERVAL != 0:
            continue

        try:
            pil_img = Image.open(fpath).convert("RGB")
        except Exception as exc:
            logger.warning("Cannot open frame %s: %s", fpath, exc)
            continue

        frame_arr = np.array(pil_img)

        # ── YOLO inference ─────────────────────────────────────────────────
        # predict() 는 PIL/np 배열 모두 허용; verbose=False 로 stdout 억제
        preds = model.predict(
            source=frame_arr,
            device=device_name,
            conf=YOLO_CONFIDENCE_THRESHOLD,
            imgsz=YOLO_IMGSZ,
            classes=YOLO_CLASS_IDS,
            verbose=False,
        )
        # boxes.xyxy: (N, 4) tensor on CPU; boxes.cls: class IDs (COCO)
        if len(preds[0].boxes) > 0:
            xyxy    = preds[0].boxes.xyxy.cpu().numpy()
            cls_ids = preds[0].boxes.cls.cpu().numpy().astype(int)
        else:
            xyxy    = np.empty((0, 4))
            cls_ids = np.empty((0,), dtype=int)

        # COCO class 0 = person → extract separately for face-exclusion masking
        person_mask  = cls_ids == 0
        person_boxes = xyxy[person_mask] if person_mask.any() else np.empty((0, 4))

        # 감지된 클래스명 수집 (쉼표 구분 문자열, 중복 제거)
        if len(cls_ids) > 0:
            detected_names = sorted({model.names[cid] for cid in cls_ids})
            detected_objects_str = ", ".join(detected_names)
        else:
            detected_objects_str = ""

        safe = _compute_safe_area(frame_arr.shape, xyxy, person_boxes=person_boxes)

        # ── scene cut ──────────────────────────────────────────────────────
        gray = cv2.cvtColor(frame_arr, cv2.COLOR_RGB2GRAY)
        cut = _is_scene_cut(prev_gray, gray)
        prev_gray = gray

        # 파일명(frame_000042.jpg)에서 절대 frame_index 파싱
        fname = Path(fpath).stem  # "frame_000042"
        m = re.search(r"(\d+)$", fname)
        abs_frame_index = int(m.group(1)) if m else list_idx

        row = {
            "frame_index":    abs_frame_index,
            "timestamp_sec":  float(abs_frame_index) / FRAME_EXTRACTION_FPS,
            "is_scene_cut":   bool(cut),
            "detected_objects": detected_objects_str,
            **safe,
        }

        if on_batch is not None:
            batch.append(row)
            if len(batch) >= batch_size:
                on_batch(batch)
                logger.info(
                    "YOLO: flushed batch of %d frames (up to %d / %d)",
                    len(batch), list_idx + 1, total,
                )
                batch = []
        else:
            results.append(row)

        if list_idx % 60 == 0:
            logger.info("YOLO: processed frame %d / %d", list_idx + 1, total)

    # 마지막 잔여 배치 플러시
    if on_batch is not None:
        if batch:
            on_batch(batch)
            logger.info(
                "YOLO: flushed final batch of %d frames (total %d processed)",
                len(batch), total,
            )
        return []

    return results
