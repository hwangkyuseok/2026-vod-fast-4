"""
benchmark_rcnn_vs_yolo.py - Faster R-CNN vs YOLOv8l 속도 비교
--------------------------------------------------------------
10분 분량(600프레임 @ 1fps) 기준 소요 시간을 측정한다.
실제 job 프레임을 사용하고, 없으면 더미 프레임으로 대체한다.

Run:
  cd D:/20.WORKSPACE/2026_VOD_FAST_4/backend
  .venv/Scripts/python.exe benchmark_rcnn_vs_yolo.py
"""

import sys
import time
import tempfile
import statistics
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))


# ─── 프레임 준비 ────────────────────────────────────────────────────────────

TARGET_MINUTES = 10
TARGET_FRAMES  = TARGET_MINUTES * 60   # 600 frames @ 1fps


def _find_real_frames(limit: int) -> list[str]:
    storage = Path(__file__).parent.parent / "storage" / "jobs"
    if not storage.exists():
        return []
    best = []
    for job_dir in sorted(storage.iterdir()):
        frames_dir = job_dir / "frames"
        if frames_dir.exists():
            paths = sorted(str(p) for p in frames_dir.glob("*.jpg"))
            if len(paths) > len(best):
                best = paths
    return best[:limit]


def _make_dummy_frames(tmp_dir: Path, count: int, width=1280, height=720) -> list[str]:
    """균일 색상 JPEG 더미 프레임을 생성한다."""
    paths = []
    print(f"  Generating {count} dummy frames ({width}x{height})...")
    for i in range(count):
        # 약간씩 다른 색으로 실제 영상에 가깝게
        r = (i * 7 + 120) % 256
        g = (i * 13 + 80)  % 256
        b = (i * 11 + 60)  % 256
        frame = np.full((height, width, 3), [b, g, r], dtype=np.uint8)
        # 10프레임마다 큰 변화 (scene cut 유발)
        if i % 60 == 0 and i > 0:
            frame[:] = [255 - b, 255 - g, 255 - r]
        p = str(tmp_dir / f"frame_{i:04d}.jpg")
        cv2.imwrite(p, frame)
        paths.append(p)
        if i % 100 == 0:
            print(f"    {i}/{count} frames generated...")
    print(f"  {count} dummy frames ready.")
    return paths


# ─── 벤치마크 실행 ──────────────────────────────────────────────────────────

def run_benchmark(module, name: str, frame_paths: list[str]) -> dict:
    """모델을 미리 워밍업한 뒤 전체 프레임을 처리하고 시간을 측정한다."""
    print(f"\n{'='*55}")
    print(f"  [{name}] Warmup (3 frames)...")

    # 워밍업: 모델 로드 + JIT 컴파일 시간 제외
    module.analyse_frames(frame_paths[:3])

    print(f"  [{name}] Benchmarking {len(frame_paths)} frames...")

    batches_count = [0]
    def _cb(batch):
        batches_count[0] += 1

    t_start = time.perf_counter()
    module.analyse_frames(frame_paths, on_batch=_cb, batch_size=200)
    elapsed = time.perf_counter() - t_start

    fps = len(frame_paths) / elapsed
    per_frame_ms = elapsed / len(frame_paths) * 1000

    result = {
        "name":          name,
        "frames":        len(frame_paths),
        "elapsed_sec":   elapsed,
        "fps":           fps,
        "per_frame_ms":  per_frame_ms,
        "batches":       batches_count[0],
    }

    print(f"  [{name}] {len(frame_paths)} frames in {elapsed:.1f}s")
    print(f"  [{name}] {fps:.2f} fps  ({per_frame_ms:.0f} ms/frame)")

    # 10분(600프레임) 기준 추정 시간
    target = TARGET_FRAMES
    est_sec = target / fps
    est_min = est_sec / 60
    print(f"  [{name}] 10min video estimate: {est_min:.1f} min  ({est_sec:.0f}s)")

    return result


# ─── 메인 ──────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  RCNN vs YOLOv8l Benchmark")
    print(f"  Target: {TARGET_FRAMES} frames ({TARGET_MINUTES} min @ 1fps)")
    print("=" * 55)

    # 프레임 준비
    real = _find_real_frames(TARGET_FRAMES)
    use_real = len(real) >= 30  # 최소 30프레임 있어야 real 사용

    with tempfile.TemporaryDirectory() as tmp:
        if use_real:
            frame_paths = real
            actual_frames = len(frame_paths)
            print(f"\nUsing REAL frames: {actual_frames} frames")
            if actual_frames < TARGET_FRAMES:
                print(f"  (fewer than {TARGET_FRAMES} - extrapolating to 10min)")
        else:
            print("\nNo real frames found. Using DUMMY frames.")
            # 벤치마크용으로 60프레임만 (더미는 실제보다 빠름 - 참고용)
            actual_frames = 60
            frame_paths = _make_dummy_frames(Path(tmp), actual_frames)
            print(f"  NOTE: Using {actual_frames} dummy frames for quick estimate.")

        # ── RCNN 벤치마크
        print("\n[1/2] Loading Faster R-CNN...")
        from step2_analysis import vision_rcnn
        rcnn_result = run_benchmark(vision_rcnn, "Faster R-CNN", frame_paths)

        # ── YOLO 벤치마크
        print("\n[2/2] Loading YOLOv8l...")
        from step2_analysis import vision_yolo
        yolo_result = run_benchmark(vision_yolo, "YOLOv8l", frame_paths)

    # ─── 비교 요약 ───────────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print("  BENCHMARK RESULTS")
    print("=" * 55)

    r = rcnn_result
    y = yolo_result

    speedup = y["fps"] / r["fps"] if r["fps"] > 0 else 0
    fps_gain = y["fps"] - r["fps"]

    print(f"\n  {'Model':<18} {'FPS':>7} {'ms/frame':>10} {'10min est.':>12}")
    print(f"  {'-'*50}")

    r_est = TARGET_FRAMES / r["fps"] / 60
    y_est = TARGET_FRAMES / y["fps"] / 60
    print(f"  {'Faster R-CNN':<18} {r['fps']:>7.2f} {r['per_frame_ms']:>10.0f} {r_est:>10.1f}min")
    print(f"  {'YOLOv8l':<18} {y['fps']:>7.2f} {y['per_frame_ms']:>10.0f} {y_est:>10.1f}min")
    print(f"  {'-'*50}")
    print(f"  {'Speedup':<18} {speedup:>7.2f}x {fps_gain:>+10.2f} {r_est - y_est:>+10.1f}min")

    print(f"\n  YOLOv8l is {speedup:.1f}x faster than Faster R-CNN (CPU)")
    print(f"  10min video: RCNN={r_est:.1f}min  YOLO={y_est:.1f}min  "
          f"(saved {r_est - y_est:.1f}min)")

    if not use_real:
        print("\n  [NOTE] Dummy frames have no real objects -> density=0 always.")
        print("         Real video may be slightly slower due to more detections.")

    print("=" * 55)


if __name__ == "__main__":
    main()
