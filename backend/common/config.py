import os
import platform
from pathlib import Path

from dotenv import load_dotenv

# backend/.env 파일이 있으면 로드 (로컬 개발 환경용)
# Docker 환경에서는 docker-compose가 환경변수를 주입하므로 이 파일이 없어도 무방
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)

_IS_WINDOWS = platform.system() == "Windows"

# ─── Database ───────────────────────────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST",     "")
DB_PORT     = int(os.getenv("DB_PORT", "5432"))
DB_NAME     = os.getenv("DB_NAME",     "")
DB_USER     = os.getenv("DB_USER",     "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_DSN      = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# ─── RabbitMQ ────────────────────────────────────────────────────────────────
RABBITMQ_HOST     = os.getenv("RABBITMQ_HOST",     "")
RABBITMQ_PORT     = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER     = os.getenv("RABBITMQ_USER",     "")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "")
RABBITMQ_URL      = (
    f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASSWORD}"
    f"@{RABBITMQ_HOST}:{RABBITMQ_PORT}/"
    f"?heartbeat=7200&blocked_connection_timeout=7200"
)

# Queue names
# QUEUE_PREFIX로 환경 간 큐 분리:
#   Windows 로컬  → 기본값 "dev"  → vod.내이름.dev.step1.preprocess ...
#   Linux/Docker  → 기본값 "prod" → vod.prod.step1.preprocess ...
# 같은 RabbitMQ 브로커를 공유할 때 메시지 혼선 방지.
# 환경변수 QUEUE_PREFIX 로 명시적 지정 가능 (e.g. "staging").
_QUEUE_PREFIX = os.getenv("QUEUE_PREFIX", "dev" if _IS_WINDOWS else "prod")

QUEUE_STEP1 = f"vod.{_QUEUE_PREFIX}.step1.preprocess"
QUEUE_STEP2 = f"vod.{_QUEUE_PREFIX}.step2.analysis"       # legacy (단일 컨테이너용, 유지)
QUEUE_STEP2A = f"vod.{_QUEUE_PREFIX}.step2a.vision"        # v2.13: 2-A (YOLO + VLM)
QUEUE_STEP2B = f"vod.{_QUEUE_PREFIX}.step2b.audio"         # v2.13: 2-B (침묵 + Whisper)
QUEUE_STEP2_GATE = f"vod.{_QUEUE_PREFIX}.step2.gate"       # v2.13: 2-C gate (Phase A)
QUEUE_STEP3 = f"vod.{_QUEUE_PREFIX}.step3.persistence"
QUEUE_STEP4 = f"vod.{_QUEUE_PREFIX}.step4.decision"

# ─── Ad Resources ────────────────────────────────────────────────────────────
AD_VIDEO_DIR = os.getenv(
    "AD_VIDEO_DIR",
    r"D:\20.WORKSPACE\2026_VOD_FAST_3\TV_CF\output"      if _IS_WINDOWS else "/ads/video",
)
AD_IMAGE_DIR = os.getenv(
    "AD_IMAGE_DIR",
    r"D:\20.WORKSPACE\2026_VOD_FAST_3\TV_CF\output_print" if _IS_WINDOWS else "/ads/banner",
)

# ─── Storage ─────────────────────────────────────────────────────────────────
STORAGE_BASE = os.getenv(
    "STORAGE_BASE",
    r"D:\20.WORKSPACE\2026_VOD_FAST_4\storage" if _IS_WINDOWS else "/app/storage",
)

# ─── VOD Source Directory ─────────────────────────────────────────────────────
VOD_DIR = os.getenv(
    "VOD_DIR",
    r"D:\20.WORKSPACE\2026_VOD_FAST_4\vod" if _IS_WINDOWS else "/vod",
)

# ─── Pipeline Parameters ─────────────────────────────────────────────────────
FRAME_EXTRACTION_FPS      = int(os.getenv("FRAME_EXTRACTION_FPS",   "1"))
SCENE_CUT_THRESHOLD       = float(os.getenv("SCENE_CUT_THRESHOLD",  "30.0"))
SILENCE_THRESHOLD_DB      = float(os.getenv("SILENCE_THRESHOLD_DB", "-40.0"))
MIN_SILENCE_DURATION_SEC  = float(os.getenv("MIN_SILENCE_DURATION_SEC", "1.0"))
# Default display time for banner-type ads
AD_BANNER_DURATION_SEC    = float(os.getenv("AD_BANNER_DURATION_SEC", "10.0"))
# Confidence threshold for Faster R-CNN detections
RCNN_CONFIDENCE_THRESHOLD = float(os.getenv("RCNN_CONFIDENCE_THRESHOLD", "0.5"))
# Qwen2-VL: sample one frame every N seconds (at 1 fps, 1 frame = 1 second)
# Longer videos automatically skip more frames to keep total samples ≤ QWEN_MAX_SAMPLES
QWEN_SAMPLE_INTERVAL_SEC  = int(os.getenv("QWEN_SAMPLE_INTERVAL_SEC",  "60"))
QWEN_MAX_SAMPLES          = int(os.getenv("QWEN_MAX_SAMPLES",          "60"))
# Faster R-CNN: flush DB insert every N frames to bound memory usage
RCNN_BATCH_SIZE           = int(os.getenv("RCNN_BATCH_SIZE",           "200"))
# YOLOv8: object detection settings
# model: yolov8n | yolov8s | yolov8m | yolov8l | yolov8x  (default: yolov8l)
YOLO_MODEL                = os.getenv("YOLO_MODEL",                "yolov8l.pt")
YOLO_CONFIDENCE_THRESHOLD = float(os.getenv("YOLO_CONFIDENCE_THRESHOLD", "0.35"))
YOLO_BATCH_SIZE           = int(os.getenv("YOLO_BATCH_SIZE",           "200"))
# imgsz: inference image size (px) — 640(default) → 800 for better small-object detection
YOLO_IMGSZ                = int(os.getenv("YOLO_IMGSZ",               "800"))
# MVP 탐지 클래스 ID (COCO 원본 기준) — 사과(53) 제외 14개
# 기본값: 16,26,39,41,45,56,57,59,60,62,63,65,67,72
_raw_class_ids            = os.getenv("YOLO_CLASS_IDS", "16,26,39,41,45,56,57,59,60,62,63,65,67,72")
YOLO_CLASS_IDS            = [int(x) for x in _raw_class_ids.split(",")]
# Whisper STT model size: tiny | base | small | medium | large
# 'small' (244M) 이상 권장 — base(74M)는 한국어 인식률이 낮아 대사가 깨짐
# v2.5+: task=transcribe + language=ko 사용 (번역 없이 한국어 원문 유지)
WHISPER_MODEL             = os.getenv("WHISPER_MODEL", "small")

# ─── Gemini Flash API ────────────────────────────────────────────────────────
# VLM_BACKEND: "qwen" (로컬 Qwen2-VL) | "gemini" (Google Gemini Flash API)
VLM_BACKEND  = os.getenv("VLM_BACKEND",  "qwen")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL",   "gemini-2.0-flash")

# ─── API Server ───────────────────────────────────────────────────────────────
API_HOST     = os.getenv("API_HOST",     "0.0.0.0")
API_PORT     = int(os.getenv("API_PORT", "8000"))
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
