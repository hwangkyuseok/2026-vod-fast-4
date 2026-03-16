"""
vision_rcnn.py — Faster R-CNN Object Detection & Safe-Area Computation
─────────────────────────────────────────────────────────────────────────
For each frame:
  1. Run Faster R-CNN (ResNet-50 FPN, COCO-pretrained) to get bounding boxes.
  2. Compute object density  = Σ(bbox areas) / frame area  (capped at 1.0)
  3. Compute safe area       = largest empty rectangle in the frame
  4. Detect scene cuts       = inter-frame mean pixel difference > threshold

Streaming / batch mode
──────────────────────
When *on_batch* callback is supplied, results are flushed every *batch_size*
frames instead of being accumulated in memory.  This bounds peak RAM usage for
long (3 h+) videos.

  • on_batch=None  (default): accumulate all results and return as a list.
  • on_batch=<fn>:            call fn(batch) every batch_size frames;
                              return [] (caller receives an empty list).

Returns per-frame dicts ready to INSERT into analysis_vision_context.
"""

import logging
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import torch
import torchvision.transforms.functional as TF
from PIL import Image
from torchvision.models.detection import (
    FasterRCNN_ResNet50_FPN_Weights,
    fasterrcnn_resnet50_fpn,
)

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from common.config import RCNN_BATCH_SIZE, RCNN_CONFIDENCE_THRESHOLD, SCENE_CUT_THRESHOLD

logger = logging.getLogger(__name__)

# ─── model (singleton, loaded on first use) ────────────────────────────────

_model = None
_device = None


def _get_model():
    global _model, _device
    if _model is None:
        _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        weights = FasterRCNN_ResNet50_FPN_Weights.DEFAULT
        _model = fasterrcnn_resnet50_fpn(weights=weights)
        _model.to(_device)
        _model.eval()
        logger.info("Faster R-CNN loaded on %s", _device)
    return _model, _device


# ─── safe-area: largest empty rectangle via histogram approach ─────────────

def _largest_safe_rectangle(occupied_mask: np.ndarray) -> tuple[int, int, int, int]:
    """
    Given a boolean occupancy mask (H×W, True = occupied),
    return (x, y, w, h) of the largest unoccupied rectangle.
    Uses the classic histogram/stack algorithm row-by-row.
    """
    h, w = occupied_mask.shape
    heights = np.zeros(w, dtype=int)
    best = (0, 0, 1, 1)  # x, y, w, h
    best_area = 0

    for row in range(h):
        # Update histogram column heights
        heights = np.where(occupied_mask[row], 0, heights + 1)

        # Largest rectangle in histogram (stack method)
        stack = []
        for col in range(w + 1):
            h_cur = heights[col] if col < w else 0
            start = col
            while stack and stack[-1][1] > h_cur:
                sc, sh = stack.pop()
                rect_w = col - sc
                rect_h = sh
                area = rect_w * rect_h
                if area > best_area:
                    best_area = area
                    best = (sc, row - sh + 1, rect_w, rect_h)
                start = sc
            stack.append((start, h_cur))

    return best


def _compute_safe_area(frame_shape: tuple, boxes: np.ndarray) -> dict:
    """
    Args:
        frame_shape: (H, W[, C])
        boxes:       np.ndarray of shape (N, 4) with [x1, y1, x2, y2]

    Returns dict with keys: safe_area_x, safe_area_y, safe_area_w, safe_area_h,
                             object_density
    """
    h, w = frame_shape[:2]
    mask = np.zeros((h, w), dtype=bool)

    total_bbox_area = 0
    for box in boxes:
        x1, y1, x2, y2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        mask[y1:y2, x1:x2] = True
        total_bbox_area += (x2 - x1) * (y2 - y1)

    object_density = min(1.0, total_bbox_area / (h * w))

    # Downsample mask to speed up the rectangle search (max 200×200)
    scale = max(1, max(h, w) // 200)
    small_mask = mask[::scale, ::scale]
    sx, sy, sw, sh = _largest_safe_rectangle(small_mask)
    # Scale back to original resolution
    # Explicitly convert to Python int — psycopg2 cannot adapt numpy.int64
    safe_x = int(sx) * scale
    safe_y = int(sy) * scale
    safe_w = int(sw) * scale
    safe_h = int(sh) * scale

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
    diff = cv2.absdiff(curr_gray, prev_gray).mean()
    return diff > SCENE_CUT_THRESHOLD


# ─── public API ────────────────────────────────────────────────────────────

def analyse_frames(
    frame_paths: list[str],
    on_batch: Callable[[list[dict]], None] | None = None,
    batch_size: int = RCNN_BATCH_SIZE,
) -> list[dict]:
    """
    Analyse a sorted list of frame image paths.

    Parameters
    ----------
    frame_paths : list[str]
        Sorted list of absolute paths to frame JPEG images.
    on_batch : Callable[[list[dict]], None] | None
        Optional callback invoked every *batch_size* frames with the current
        batch.  When provided, results are NOT accumulated in memory — the
        function returns an empty list.  Use this for long videos to keep
        memory usage bounded.
    batch_size : int
        Number of frames to accumulate before flushing via *on_batch*.
        Defaults to RCNN_BATCH_SIZE from config (200).

    Returns
    -------
    list[dict]
        Per-frame dicts with keys:
            frame_index, timestamp_sec,
            safe_area_x, safe_area_y, safe_area_w, safe_area_h,
            object_density, is_scene_cut
        Empty list when *on_batch* is provided (results streamed to callback).
    """
    model, device = _get_model()
    results: list[dict] = []
    batch: list[dict] = []
    prev_gray = None

    sorted_paths = sorted(frame_paths)
    total = len(sorted_paths)

    for idx, fpath in enumerate(sorted_paths):
        try:
            pil_img = Image.open(fpath).convert("RGB")
        except Exception as exc:
            logger.warning("Cannot open frame %s: %s", fpath, exc)
            continue

        # ── inference ──────────────────────────────────────────────────────
        tensor = TF.to_tensor(pil_img).to(device)
        with torch.no_grad():
            predictions = model([tensor])
        pred = predictions[0]

        keep = pred["scores"] > RCNN_CONFIDENCE_THRESHOLD
        boxes = pred["boxes"][keep].cpu().numpy()

        frame_arr = np.array(pil_img)
        safe = _compute_safe_area(frame_arr.shape, boxes)

        # ── scene cut ──────────────────────────────────────────────────────
        gray = cv2.cvtColor(frame_arr, cv2.COLOR_RGB2GRAY)
        cut = _is_scene_cut(prev_gray, gray)
        prev_gray = gray

        row = {
            "frame_index":    int(idx),            # ensure Python int, not numpy
            "timestamp_sec":  float(idx),          # 1 fps → idx == seconds
            "is_scene_cut":   bool(cut),
            **safe,                                # already Python int/float
        }

        if on_batch is not None:
            batch.append(row)
            if len(batch) >= batch_size:
                on_batch(batch)
                logger.info(
                    "RCNN: flushed batch of %d frames (up to frame %d / %d)",
                    len(batch), idx + 1, total,
                )
                batch = []
        else:
            results.append(row)

        if idx % 60 == 0:
            logger.info("RCNN: processed frame %d / %d", idx + 1, total)

    # Flush any remaining frames in the last (partial) batch
    if on_batch is not None:
        if batch:
            on_batch(batch)
            logger.info(
                "RCNN: flushed final batch of %d frames (total %d processed)",
                len(batch), total,
            )
        return []

    return results
