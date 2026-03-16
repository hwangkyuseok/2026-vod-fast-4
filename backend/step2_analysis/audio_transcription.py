"""
audio_transcription.py -- Speech-to-Text via OpenAI Whisper
────────────────────────────────────────────────────────────
Transcribes the extracted WAV file and returns timestamped segments.

task='transcribe' + language='ko' 로 한국어 원문 그대로 유지.
  - v2.5 이전: task='translate' (영어 변환) — target_mood 영어 키워드 매칭용
  - v2.5 이후: Narrative 1:1 코사인 유사도 매칭으로 전환 → 번역 불필요
    dialogue_segmenter는 paraphrase-multilingual-MiniLM-L12-v2 (한국어 지원)를 사용
    vision_qwen는 한국어 대사를 직접 수신하여 씬 컨텍스트 생성

language='ko' 명시로 배경음악/소음 환경에서 발생하는 언어 오감지 방지.

Returns a list of dicts:
    [{"start_sec": float, "end_sec": float, "text": str}, ...]

Dependencies:
    pip install openai-whisper
"""

import logging
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common.config import WHISPER_MODEL

logger = logging.getLogger(__name__)

_model_cache: dict = {}


def _load_model(model_name: str):
    """Load and cache the Whisper model (loaded once per process)."""
    if model_name not in _model_cache:
        import whisper
        logger.info("Loading Whisper model '%s' ...", model_name)
        _model_cache[model_name] = whisper.load_model(model_name)
        logger.info("Whisper model '%s' loaded.", model_name)
    return _model_cache[model_name]


def transcribe(audio_path: str) -> list[dict]:
    """
    Transcribe *audio_path* and return a list of timestamped text segments.

    Parameters
    ----------
    audio_path : str
        Path to the WAV file extracted in Step 1.

    Returns
    -------
    list[dict]
        Each dict has keys: start_sec (float), end_sec (float), text (str).
        Returns an empty list if transcription fails or produces no segments.
    """
    path = Path(audio_path)
    if not path.exists():
        logger.warning("Audio file not found, skipping transcription: %s", audio_path)
        return []

    model = _load_model(WHISPER_MODEL)

    logger.info("Transcribing audio: %s (model=%s)", audio_path, WHISPER_MODEL)
    try:
        result = model.transcribe(
            str(path),
            task="transcribe",  # 원문 유지 (v2.5+ Narrative 매칭은 번역 불필요)
            language="ko",      # 한국어 강제 지정 — 배경음악/소음으로 인한 언어 오감지 방지
            fp16=False,         # fp32 for CPU compatibility
            verbose=False,
        )
    except Exception as exc:
        logger.error("Whisper transcription failed: %s", exc)
        return []

    segments = []
    for seg in result.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        segments.append({
            "start_sec": round(float(seg["start"]), 3),
            "end_sec":   round(float(seg["end"]),   3),
            "text":      text,
        })

    logger.info(
        "Transcription complete: %d segment(s), ~%d words",
        len(segments),
        sum(len(s["text"].split()) for s in segments),
    )
    return segments
