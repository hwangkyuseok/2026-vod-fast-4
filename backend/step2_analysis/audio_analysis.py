"""
audio_analysis.py — Speech / Silence Segmentation
────────────────────────────────────────────────────
Uses librosa to detect silence intervals in the extracted WAV file.

Returns a list of dicts:
    [{"silence_start_sec": float, "silence_end_sec": float}, …]
"""

import logging
from pathlib import Path

import librosa
import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from common.config import MIN_SILENCE_DURATION_SEC, SILENCE_THRESHOLD_DB

logger = logging.getLogger(__name__)

FRAME_DURATION_MS  = 25    # analysis window (ms)
HOP_DURATION_MS    = 10    # hop size (ms)


def detect_silence(audio_path: str) -> list[dict]:
    """
    Detect silence intervals in *audio_path*.

    Returns a list of dicts with keys ``silence_start_sec`` and
    ``silence_end_sec`` for every contiguous silent segment that
    is at least MIN_SILENCE_DURATION_SEC seconds long.
    """
    logger.info("Loading audio: %s", audio_path)
    y, sr = librosa.load(audio_path, sr=None, mono=True)

    frame_length = int(sr * FRAME_DURATION_MS / 1000)
    hop_length   = int(sr * HOP_DURATION_MS   / 1000)

    rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)[0]
    # Avoid log(0)
    rms = np.where(rms == 0, 1e-10, rms)
    db  = librosa.amplitude_to_db(rms, ref=np.max)

    silence_mask = db < SILENCE_THRESHOLD_DB

    intervals: list[dict] = []
    in_silence   = False
    start_frame  = 0

    for frame_idx, is_silent in enumerate(silence_mask):
        if is_silent and not in_silence:
            in_silence  = True
            start_frame = frame_idx
        elif not is_silent and in_silence:
            in_silence = False
            start_sec  = float(librosa.frames_to_time(start_frame, sr=sr, hop_length=hop_length))
            end_sec    = float(librosa.frames_to_time(frame_idx,   sr=sr, hop_length=hop_length))
            if (end_sec - start_sec) >= MIN_SILENCE_DURATION_SEC:
                intervals.append({"silence_start_sec": round(start_sec, 3),
                                   "silence_end_sec":   round(end_sec,   3)})

    # Handle trailing silence
    if in_silence:
        start_sec = float(librosa.frames_to_time(start_frame, sr=sr, hop_length=hop_length))
        end_sec   = float(len(y) / sr)
        if (end_sec - start_sec) >= MIN_SILENCE_DURATION_SEC:
            intervals.append({"silence_start_sec": round(start_sec, 3),
                               "silence_end_sec":   round(end_sec,   3)})

    logger.info("Detected %d silence interval(s) in %s", len(intervals), audio_path)
    return intervals
