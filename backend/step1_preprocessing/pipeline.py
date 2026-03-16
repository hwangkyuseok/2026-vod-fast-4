"""
Step 1 — Preprocessing Pipeline
─────────────────────────────────
• Demux: extract audio (.wav) from the source video
• Frame extraction: 1 fps → JPEG images saved under storage/jobs/{job_id}/frames/
• Persist metadata to video_preprocessing_info + update job_history
• Publish job_id to the QUEUE_STEP2 queue

Entry-point as a service:
    python -m step1_preprocessing.pipeline --consume

One-shot (used by the API server to start a job immediately):
    from step1_preprocessing.pipeline import run
    run(job_id, video_path)
"""

import argparse
import json
import logging
import os
import subprocess
import uuid
from pathlib import Path

import cv2
import ffmpeg

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import config
from common import db as _db
from common import rabbitmq as mq
from common.logging_setup import setup_logging

setup_logging("step1")
logger = logging.getLogger(__name__)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _job_storage_dir(job_id: str) -> Path:
    p = Path(config.STORAGE_BASE) / "jobs" / job_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _update_job_status(job_id: str, status: str, error: str | None = None) -> None:
    _db.execute(
        """
        UPDATE job_history
           SET status = %s,
               error_message = %s,
               updated_at = NOW()
         WHERE job_id = %s
        """,
        (status, error, job_id),
    )


def extract_audio(video_path: str, output_dir: Path) -> str:
    """Demux audio stream to WAV.  Returns the output file path."""
    out_path = str(output_dir / "audio.wav")
    (
        ffmpeg
        .input(video_path)
        .output(out_path, acodec="pcm_s16le", ac=1, ar="16000")
        .overwrite_output()
        .run(quiet=True)
    )
    logger.info("Audio extracted -> %s", out_path)
    return out_path


def extract_frames(video_path: str, output_dir: Path, fps: int = 1) -> str:
    """Extract frames at *fps* Hz.  Returns the frame directory path."""
    frame_dir = output_dir / "frames"
    frame_dir.mkdir(exist_ok=True)
    pattern = str(frame_dir / "frame_%06d.jpg")
    (
        ffmpeg
        .input(video_path)
        .filter("fps", fps=fps)
        .output(pattern, qscale=2)
        .overwrite_output()
        .run(quiet=True)
    )
    count = len(list(frame_dir.glob("*.jpg")))
    logger.info("Frames extracted (%d frames @ %d fps) -> %s", count, fps, frame_dir)
    return str(frame_dir)


def get_video_metadata(video_path: str) -> dict:
    """Return duration, fps, width, height, total_frames via ffprobe."""
    probe = ffmpeg.probe(video_path)
    vs = next(s for s in probe["streams"] if s["codec_type"] == "video")
    duration = float(probe["format"]["duration"])
    fps_str = vs.get("r_frame_rate", "25/1")
    num, den = fps_str.split("/")
    fps = float(num) / float(den)
    width = int(vs["width"])
    height = int(vs["height"])
    total_frames = int(duration * config.FRAME_EXTRACTION_FPS)
    return {
        "duration_sec": duration,
        "fps": fps,
        "width": width,
        "height": height,
        "total_frames": total_frames,
    }


def save_to_db(
    job_id: str,
    original_video_path: str,
    audio_path: str,
    frame_dir_path: str,
    meta: dict,
) -> None:
    _db.execute(
        """
        INSERT INTO video_preprocessing_info
            (job_id, original_video_path, audio_path, frame_dir_path,
             duration_sec, fps, width, height, total_frames)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (
            job_id,
            original_video_path,
            audio_path,
            frame_dir_path,
            meta["duration_sec"],
            meta["fps"],
            meta["width"],
            meta["height"],
            meta["total_frames"],
        ),
    )
    logger.info("Preprocessing info saved for job %s", job_id)


# ─── main run function ────────────────────────────────────────────────────────

def run(job_id: str, video_path: str) -> None:
    """Execute the full Step-1 pipeline for *job_id*."""
    _update_job_status(job_id, "preprocessing")
    try:
        storage_dir = _job_storage_dir(job_id)

        audio_path  = extract_audio(video_path, storage_dir)
        frame_dir   = extract_frames(video_path, storage_dir, fps=config.FRAME_EXTRACTION_FPS)
        meta        = get_video_metadata(video_path)

        save_to_db(job_id, video_path, audio_path, frame_dir, meta)
        _update_job_status(job_id, "analysing")

        mq.publish(config.QUEUE_STEP2, {"job_id": job_id})
        logger.info("Step-1 complete - published to %s", config.QUEUE_STEP2)

    except Exception as exc:
        _update_job_status(job_id, "failed", str(exc))
        logger.exception("Step-1 failed for job %s: %s", job_id, exc)
        raise


# ─── RabbitMQ consumer entry-point ───────────────────────────────────────────

def _on_message(payload: dict) -> None:
    job_id     = payload["job_id"]
    video_path = payload.get("video_path")

    if not video_path:
        row = _db.fetchone(
            "SELECT input_video_path FROM job_history WHERE job_id = %s",
            (job_id,),
        )
        if row is None:
            raise ValueError(f"Unknown job_id: {job_id}")
        video_path = row["input_video_path"]

    run(job_id, video_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Step-1 Preprocessing Service")
    parser.add_argument("--consume", action="store_true",
                        help="Run as a blocking RabbitMQ consumer")
    parser.add_argument("--job-id",    help="Run one-shot with this job_id")
    parser.add_argument("--video-path", help="Path to the source video")
    args = parser.parse_args()

    if args.consume:
        # ack_early=True: Step-1 (ffmpeg extraction) can take several minutes
        # for large videos. Acknowledge before processing to prevent the broker
        # from redelivering due to consumer_timeout.
        mq.consume(config.QUEUE_STEP1, _on_message, ack_early=True)
    elif args.job_id and args.video_path:
        run(args.job_id, args.video_path)
    else:
        parser.print_help()
