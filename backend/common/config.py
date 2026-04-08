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

QUEUE_STEP1  = f"vod.jimin.{_QUEUE_PREFIX}.step1.preprocess"
QUEUE_STEP2  = f"vod.jimin.{_QUEUE_PREFIX}.step2.analysis"       # legacy (유지)
QUEUE_STEP2A = f"vod.jimin.{_QUEUE_PREFIX}.step2a.audio"         # v2.15: 2-A (오디오 우선: faster-whisper + SBERT 분절)
QUEUE_STEP2B = f"vod.jimin.{_QUEUE_PREFIX}.step2b.vision"        # v2.15: 2-B (비전 후속: 씬별 YOLO + Gemini)
QUEUE_STEP3  = f"vod.jimin.{_QUEUE_PREFIX}.step3.persistence"
QUEUE_STEP4  = f"vod.jimin.{_QUEUE_PREFIX}.step4.decision"

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
# OpenCV 모션 탐지 파라미터 (튜닝 완료)
# threshold=30: 프레임 간 픽셀 차이 임계값 (scene cut 판정)
# frame_interval=5: N프레임마다 1프레임 처리 (속도/정확도 균형)
SCENE_CUT_THRESHOLD       = float(os.getenv("SCENE_CUT_THRESHOLD",  "30.0"))
OPENCV_FRAME_INTERVAL     = int(os.getenv("OPENCV_FRAME_INTERVAL",  "5"))
# Silero VAD 파라미터 (튜닝 완료, librosa 대비 BGM 환경에서 우수)
# threshold=0.5: 음성 감지 확률 임계값
# min_silence_ms=1000: 최소 침묵 구간 (ms)
VAD_THRESHOLD             = float(os.getenv("VAD_THRESHOLD",        "0.5"))
VAD_MIN_SILENCE_MS        = int(os.getenv("VAD_MIN_SILENCE_MS",     "1000"))
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
# MVP 탐지 클래스 ID (COCO 0-indexed) — 15개
# dog(16) handbag(26) bottle(39) cup(41) bowl(45) pizza(53)
# chair(56) couch(57) bed(59) dining_table(60) tv(62) laptop(63)
# remote(65) cell_phone(67) refrigerator(72)
_raw_class_ids            = os.getenv("YOLO_CLASS_IDS", "16,26,39,41,45,53,56,57,59,60,62,63,65,67,72")
YOLO_CLASS_IDS            = [int(x) for x in _raw_class_ids.split(",")]
# Whisper STT model size (legacy openai-whisper, 하위 호환용)
WHISPER_MODEL             = os.getenv("WHISPER_MODEL", "small")
# faster-whisper 모델 (Step2-A 전용)
# large-v3 권장 — VAD 필터 포함, 한국어 정확도 최고
FASTER_WHISPER_MODEL      = os.getenv("FASTER_WHISPER_MODEL", "large-v3")
# SBERT 씬 분절 설정 (Step2-A 전용)
SBERT_MODEL               = os.getenv("SBERT_MODEL",            "jhgan/ko-sroberta-multitask")
SBERT_SILENCE_GAP_SEC     = float(os.getenv("SBERT_SILENCE_GAP_SEC", "4.0"))
SBERT_SIM_THRESHOLD       = float(os.getenv("SBERT_SIM_THRESHOLD",   "0.3"))
# Gemini 씬별 프레임 샘플 수 (Step2-B 전용)
SCENE_SAMPLE_FRAMES       = int(os.getenv("SCENE_SAMPLE_FRAMES", "5"))  # 개선 6: 3→5 (safe_area/density 정밀도 향상)

# ─── Cross-Encoder Model ─────────────────────────────────────────────────────
# 로컬(Windows): step4_decision/model/ 상대경로 사용
# 서버(Linux/Docker): /app/storage/models/cross_encoder (bind mount)
CROSS_ENCODER_MODEL_DIR = os.getenv(
    "CROSS_ENCODER_MODEL_DIR",
    str(Path(__file__).parent.parent / "step4_decision" / "model") if _IS_WINDOWS
    else "/app/storage/models/cross_encoder",
)

# ─── Gemini Flash API ────────────────────────────────────────────────────────
# VLM_BACKEND: "qwen" (로컬 Qwen2-VL) | "gemini" (Google Gemini Flash API)
VLM_BACKEND  = os.getenv("VLM_BACKEND",  "qwen")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL",   "gemini-2.5-flash-preview-04-17")
# RPM 제한 준수를 위한 호출 간 최소 대기 시간 (초)
#   무료 티어 15 RPM  → 4.0s
#   유료 티어 1000 RPM → 0.1s (600 RPM 기준 보수적 값)
GEMINI_RPM_INTERVAL = float(os.getenv("GEMINI_RPM_INTERVAL", "0.1"))

# ─── API Server ───────────────────────────────────────────────────────────────
API_HOST     = os.getenv("API_HOST",     "0.0.0.0")
API_PORT     = int(os.getenv("API_PORT", "8000"))
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
