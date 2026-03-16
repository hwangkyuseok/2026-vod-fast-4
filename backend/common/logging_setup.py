"""
Shared logging configuration for all pipeline services.

Each service calls setup_logging(service_name) once at startup.

Log files are written to:
  {STORAGE_BASE}/logs/{service_name}.log   (midnight rotation, 14-day retention)

Console output is preserved so existing terminal monitoring still works.
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from common.config import STORAGE_BASE

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(service_name: str, level: int = logging.INFO) -> None:
    """
    Configure the root logger with a console handler and a rotating file handler.

    Parameters
    ----------
    service_name : str
        Short identifier used as the log filename (e.g. "step1", "step5_api").
    level : int
        Root logging level (default: INFO).
    """
    log_dir = Path(STORAGE_BASE) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{service_name}.log"

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # ── File handler — midnight rotation, 14 backup files ────────────────────
    file_handler = TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        backupCount=14,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    # ── Console handler ───────────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    # ── Root logger ───────────────────────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(level)
    # Clear existing handlers to prevent duplicates on repeated calls
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Completely silence pika to prevent CRITICAL spam when RabbitMQ closes a
    # channel mid-stream (e.g. consumer_timeout after long AI processing).
    # The relevant warnings are already re-logged by common.rabbitmq itself.
    _pika = logging.getLogger("pika")
    _pika.handlers.clear()
    _pika.addHandler(logging.NullHandler())
    _pika.propagate = False

    # Reduce noise from other verbose third-party libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("transformers").setLevel(logging.WARNING)
    logging.getLogger("torch").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging initialised - service=%s  log=%s", service_name, log_file
    )
