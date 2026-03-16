"""
populate_ad_inventory.py
─────────────────────────
Scans the ad video and image directories and inserts records into
ad_inventory.  Safe to re-run — uses ON CONFLICT DO NOTHING.

Usage:
    python populate_ad_inventory.py
"""

import os
import subprocess
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).parent))
from common.config import AD_IMAGE_DIR, AD_VIDEO_DIR, AD_BANNER_DURATION_SEC, DB_DSN
from common import db as _db

# ─── helpers ──────────────────────────────────────────────────────────────────

def _video_duration(path: str) -> float | None:
    """Use ffprobe to get duration of a video file."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


# ─── main ─────────────────────────────────────────────────────────────────────

def populate() -> None:
    inserted = 0

    # ── Video clips ────────────────────────────────────────────────────────────
    video_dir = Path(AD_VIDEO_DIR)
    for mp4_file in sorted(video_dir.glob("*.mp4")):
        filepath   = str(mp4_file)
        ad_id      = mp4_file.stem          # full filename without extension as ID
        duration   = _video_duration(filepath)

        _db.execute(
            """
            INSERT INTO ad_inventory
                (ad_id, ad_name, ad_type, resource_path, duration_sec)
            VALUES (%s, %s, 'video_clip', %s, %s)
            ON CONFLICT (ad_id) DO NOTHING
            """,
            (ad_id, mp4_file.stem, filepath, duration),
        )
        inserted += 1
        print(f"  [video] {mp4_file.name} — duration={duration:.1f}s")

    # ── Banner images ──────────────────────────────────────────────────────────
    image_dir = Path(AD_IMAGE_DIR)
    for img_file in sorted(image_dir.glob("*.jpg")) + sorted(image_dir.glob("*.png")):
        filepath = str(img_file)
        ad_id    = img_file.stem

        _db.execute(
            """
            INSERT INTO ad_inventory
                (ad_id, ad_name, ad_type, resource_path, duration_sec)
            VALUES (%s, %s, 'banner', %s, %s)
            ON CONFLICT (ad_id) DO NOTHING
            """,
            (ad_id, img_file.stem, filepath, AD_BANNER_DURATION_SEC),
        )
        inserted += 1
        print(f"  [banner] {img_file.name}")

    print(f"\n✅  Inserted (or skipped) {inserted} ad entries.")


if __name__ == "__main__":
    populate()
