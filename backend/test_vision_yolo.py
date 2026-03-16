"""
test_vision_yolo.py - YOLOv8l module unit tests + debugging
------------------------------------------------------------
Run:
  cd D:/20.WORKSPACE/2026_VOD_FAST_4/backend
  .venv/Scripts/python.exe test_vision_yolo.py

테스트 항목:
  1. import & 모델 로드
  2. 단일 프레임 추론 결과 구조 검증
  3. safe_area 유효성 (0 <= x+w <= W, 0 <= y+h <= H)
  4. object_density 범위 (0.0 ~ 1.0)
  5. scene_cut 필드 타입 (bool)
  6. on_batch 콜백 모드 (배치 플러시 확인)
  7. RCNN 인터페이스 동등성 (반환 dict key 집합 동일)
  8. 빈 boxes 케이스 (객체 없는 프레임)
  9. 다중 프레임 순서 정렬 확인
 10. 성능 측정 (frames/sec)
"""

import sys
import time
import tempfile
import logging
from pathlib import Path

import cv2
import numpy as np

# ─── 경로 설정 ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("test_vision_yolo")

# ─── 테스트용 더미 프레임 생성 ─────────────────────────────────────────────

def _make_frame(width: int = 640, height: int = 360, color: tuple = (100, 150, 200)) -> np.ndarray:
    """단색 BGR 프레임 생성 (opencv imwrite 기본 포맷)"""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = color  # BGR
    return frame


def _make_frame_with_rect(
    width: int = 640,
    height: int = 360,
    rect: tuple = (100, 80, 200, 160),
) -> np.ndarray:
    """객체 사각형이 있는 프레임 (흰 배경 + 검은 사각형)"""
    frame = np.full((height, width, 3), 200, dtype=np.uint8)
    x1, y1, x2, y2 = rect
    cv2.rectangle(frame, (x1, y1), (x2, y2), (30, 30, 30), -1)
    return frame


def save_test_frames(tmp_dir: Path, count: int = 5) -> list[str]:
    """count 개의 테스트 JPEG 프레임을 임시 디렉토리에 저장한다."""
    paths = []
    for i in range(count):
        # 각 프레임마다 살짝 다른 색 (scene cut 감지 테스트용)
        b, g, r = (i * 20) % 200, (i * 30 + 50) % 200, (i * 15 + 100) % 200
        frame = _make_frame(color=(b, g, r))
        # 3번째 프레임에는 큰 변화 (scene cut 유발)
        if i == 2:
            frame = _make_frame(color=(240, 10, 10))
        fpath = tmp_dir / f"frame_{i:04d}.jpg"
        cv2.imwrite(str(fpath), frame)
        paths.append(str(fpath))
    return paths


# ─── 실제 영상 프레임 사용 (존재하는 경우) ────────────────────────────────

def _find_real_frames(limit: int = 10) -> list[str]:
    """storage/jobs 에서 첫 번째 job 의 프레임 최대 limit 개를 반환한다."""
    storage = ROOT.parent / "storage" / "jobs"
    if not storage.exists():
        return []
    for job_dir in sorted(storage.iterdir()):
        frames_dir = job_dir / "frames"
        if frames_dir.exists():
            paths = sorted(str(p) for p in frames_dir.glob("*.jpg"))[:limit]
            if paths:
                logger.info("Real frames found: %d in %s", len(paths), frames_dir)
                return paths
    return []


# ─── 테스트 함수들 ─────────────────────────────────────────────────────────

PASS = "[PASS]"
FAIL = "[FAIL]"
results = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = PASS if condition else FAIL
    msg = f"{status} {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    results.append((name, condition))


def test_import():
    print("\n=== Test 1: Import & Model Load ===")
    try:
        from step2_analysis import vision_yolo
        check("vision_yolo imports without error", True)
        model, device = vision_yolo._get_model()
        check("model loaded", model is not None)
        check("device_name is str", isinstance(device, str))
        print(f"  Device: {device}")
        return vision_yolo
    except Exception as e:
        check("vision_yolo imports without error", False, str(e))
        return None


def test_single_frame(vision_yolo, frame_paths: list[str]):
    print("\n=== Test 2: Single Frame Inference ===")
    try:
        result = vision_yolo.analyse_frames([frame_paths[0]])
        check("returns list", isinstance(result, list))
        check("returns 1 row", len(result) == 1)
        row = result[0]

        expected_keys = {
            "frame_index", "timestamp_sec",
            "safe_area_x", "safe_area_y", "safe_area_w", "safe_area_h",
            "object_density", "is_scene_cut",
        }
        check("dict has all required keys", expected_keys.issubset(row.keys()),
              f"keys={set(row.keys())}")
        check("frame_index == 0", row["frame_index"] == 0)
        check("timestamp_sec == 0.0", row["timestamp_sec"] == 0.0)
        check("is_scene_cut is bool", isinstance(row["is_scene_cut"], bool))
        check("is_scene_cut == False (first frame)",
              row["is_scene_cut"] is False)

        print(f"  Row: {row}")
        return True
    except Exception as e:
        check("single frame inference", False, str(e))
        import traceback; traceback.print_exc()
        return False


def test_safe_area_validity(vision_yolo, frame_paths: list[str]):
    print("\n=== Test 3: Safe Area Validity ===")
    try:
        from PIL import Image
        pil = Image.open(frame_paths[0])
        W, H = pil.size

        result = vision_yolo.analyse_frames([frame_paths[0]])
        row = result[0]

        x, y, w, h = row["safe_area_x"], row["safe_area_y"], row["safe_area_w"], row["safe_area_h"]
        check("safe_area_x >= 0", x >= 0, f"x={x}")
        check("safe_area_y >= 0", y >= 0, f"y={y}")
        check("safe_area_w > 0", w > 0, f"w={w}")
        check("safe_area_h > 0", h > 0, f"h={h}")
        check("x + w <= W", x + w <= W, f"{x}+{w}={x+w} vs W={W}")
        check("y + h <= H", y + h <= H, f"{y}+{h}={y+h} vs H={H}")
        print(f"  Frame size: {W}x{H}, safe_area: ({x},{y},{w},{h})")
    except Exception as e:
        check("safe_area validity", False, str(e))
        import traceback; traceback.print_exc()


def test_object_density(vision_yolo, frame_paths: list[str]):
    print("\n=== Test 4: Object Density Range ===")
    try:
        result = vision_yolo.analyse_frames(frame_paths[:3])
        for row in result:
            d = row["object_density"]
            check(
                f"density in [0.0, 1.0] frame={row['frame_index']}",
                0.0 <= d <= 1.0,
                f"density={d}",
            )
    except Exception as e:
        check("object density range", False, str(e))


def test_scene_cut(vision_yolo, frame_paths: list[str]):
    print("\n=== Test 5: Scene Cut Detection ===")
    try:
        result = vision_yolo.analyse_frames(frame_paths)
        cuts = [r for r in result if r["is_scene_cut"]]
        print(f"  Total frames: {len(result)}, scene cuts detected: {len(cuts)}")
        check("first frame is never scene cut", result[0]["is_scene_cut"] is False)
        check("is_scene_cut type is bool for all frames",
              all(isinstance(r["is_scene_cut"], bool) for r in result))
    except Exception as e:
        check("scene cut detection", False, str(e))


def test_batch_callback(vision_yolo, frame_paths: list[str]):
    print("\n=== Test 6: on_batch Callback Mode ===")
    try:
        batches_received = []

        def _cb(batch):
            batches_received.append(len(batch))

        # batch_size=2 로 강제해 배치 분리 유도
        returned = vision_yolo.analyse_frames(frame_paths, on_batch=_cb, batch_size=2)

        check("returned list is empty in batch mode", returned == [])
        check("at least 1 batch received", len(batches_received) >= 1)
        total_flushed = sum(batches_received)
        check(
            f"total flushed == total frames ({len(frame_paths)})",
            total_flushed == len(frame_paths),
            f"flushed={total_flushed}",
        )
        print(f"  Batches: {batches_received}  (batch_size=2, total={total_flushed})")
    except Exception as e:
        check("batch callback mode", False, str(e))
        import traceback; traceback.print_exc()


def test_rcnn_interface_parity(vision_yolo):
    """vision_rcnn 과 반환 dict key 집합이 동일한지 확인 (drop-in 호환성)"""
    print("\n=== Test 7: RCNN Interface Parity ===")
    try:
        from step2_analysis import vision_rcnn
        # 더미 1프레임으로 비교
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            frame = _make_frame()
            fpath = str(tmp_path / "frame_0000.jpg")
            cv2.imwrite(fpath, frame)

            yolo_result = vision_yolo.analyse_frames([fpath])
            rcnn_result = vision_rcnn.analyse_frames([fpath])

            yolo_keys = set(yolo_result[0].keys()) if yolo_result else set()
            rcnn_keys = set(rcnn_result[0].keys()) if rcnn_result else set()

            check("YOLO keys == RCNN keys", yolo_keys == rcnn_keys,
                  f"YOLO={yolo_keys}, RCNN={rcnn_keys}")
            print(f"  Shared keys: {sorted(yolo_keys)}")
    except Exception as e:
        check("RCNN interface parity", False, str(e))
        import traceback; traceback.print_exc()


def test_empty_frame(vision_yolo):
    """객체가 전혀 없는 단색 프레임 (density=0 기대)"""
    print("\n=== Test 8: Empty Frame (no objects) ===")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            frame = np.full((360, 640, 3), 180, dtype=np.uint8)  # 균일 회색
            fpath = str(tmp_path / "frame_0000.jpg")
            cv2.imwrite(fpath, frame)

            result = vision_yolo.analyse_frames([fpath])
            row = result[0]

            check("returns 1 row", len(result) == 1)
            check("object_density is float", isinstance(row["object_density"], float))
            check("safe_area_w > 0", row["safe_area_w"] > 0)
            check("safe_area_h > 0", row["safe_area_h"] > 0)
            print(f"  density={row['object_density']}, safe=({row['safe_area_x']},{row['safe_area_y']},{row['safe_area_w']},{row['safe_area_h']})")
    except Exception as e:
        check("empty frame", False, str(e))


def test_frame_order(vision_yolo):
    """frame_index 가 정렬 순서로 0,1,2,...를 반환하는지 확인"""
    print("\n=== Test 9: Frame Index Order ===")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = []
            # 역순으로 파일명 생성 (정렬 후 올바른 순서인지 검증)
            for i in [3, 1, 4, 0, 2]:
                frame = _make_frame(color=(i * 30, i * 20, i * 10))
                p = str(tmp_path / f"frame_{i:04d}.jpg")
                cv2.imwrite(p, frame)
                paths.append(p)

            result = vision_yolo.analyse_frames(paths)
            indices = [r["frame_index"] for r in result]
            check("frame indices are 0,1,2,3,4", indices == [0, 1, 2, 3, 4],
                  f"got {indices}")
    except Exception as e:
        check("frame order", False, str(e))


def test_performance(vision_yolo, frame_paths: list[str]):
    print("\n=== Test 10: Performance (fps) ===")
    try:
        n = min(10, len(frame_paths))
        test_paths = frame_paths[:n]

        t0 = time.perf_counter()
        result = vision_yolo.analyse_frames(test_paths)
        elapsed = time.perf_counter() - t0

        fps = n / elapsed if elapsed > 0 else float("inf")
        check(f"processed {n} frames successfully", len(result) == n)
        print(f"  {n} frames in {elapsed:.2f}s = {fps:.2f} fps")

        # CPU 환경 기준 최소 기대치 (YOLOv8l CPU 는 약 0.5~3 fps 예상)
        min_fps = 0.1
        check(f"fps > {min_fps}", fps > min_fps, f"fps={fps:.3f}")
    except Exception as e:
        check("performance", False, str(e))


# ─── 실행 ──────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  VOD Ad Overlay - vision_yolo.py Unit Tests")
    print("=" * 60)

    # 테스트 프레임 준비
    real_frames = _find_real_frames(limit=10)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dummy_frames = save_test_frames(tmp_path, count=5)
        frame_paths = real_frames if real_frames else dummy_frames
        mode = "real" if real_frames else "dummy"
        print(f"\nUsing {mode} frames: {len(frame_paths)} files")

        # 테스트 순서 실행
        vision_yolo = test_import()
        if vision_yolo is None:
            print("\n[FATAL] Import failed - aborting remaining tests")
            return

        test_single_frame(vision_yolo, frame_paths)
        test_safe_area_validity(vision_yolo, frame_paths)
        test_object_density(vision_yolo, frame_paths)
        test_scene_cut(vision_yolo, frame_paths)
        test_batch_callback(vision_yolo, frame_paths)
        test_rcnn_interface_parity(vision_yolo)
        test_empty_frame(vision_yolo)
        test_frame_order(vision_yolo)
        test_performance(vision_yolo, frame_paths)

    # ─── 결과 요약 ───────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  Test Summary")
    print("=" * 60)
    passed = sum(1 for _, ok in results if ok)
    failed = sum(1 for _, ok in results if not ok)
    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n  Total: {len(results)}  Passed: {passed}  Failed: {failed}")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
