"""
Migration: add context_tags column to analysis_audio table.

Run once before deploying the updated consumer.py:
    python migrate_add_context_tags.py

Safe to run multiple times (uses IF NOT EXISTS / DO NOTHING logic).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import db as _db
from common.logging_setup import setup_logging

setup_logging("migration")

import logging
logger = logging.getLogger(__name__)


def migrate() -> None:
    logger.info("Adding context_tags and context_summary columns to analysis_audio ...")
    _db.execute(
        """
        ALTER TABLE analysis_audio
        ADD COLUMN IF NOT EXISTS context_tags TEXT[]
        """
    )
    _db.execute(
        """
        ALTER TABLE analysis_audio
        ADD COLUMN IF NOT EXISTS context_summary TEXT
        """
    )
    logger.info("Migration complete.")


if __name__ == "__main__":
    migrate()
