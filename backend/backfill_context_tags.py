"""
Backfill context_tags for existing completed jobs.

Run after migrate_add_context_tags.py to generate context tags for jobs
that were processed before this feature was added.

Usage:
    python backfill_context_tags.py                  # all jobs missing context_tags
    python backfill_context_tags.py --job <job_id>   # specific job
    python backfill_context_tags.py --limit 5        # first N jobs
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import db as _db
from common.logging_setup import setup_logging
from step2_analysis import vision_qwen, audio_transcription, dialogue_segmenter

setup_logging("backfill")
logger = logging.getLogger(__name__)


def _get_jobs_needing_context(job_id: str | None, limit: int | None) -> list[str]:
    """Return job_ids that have silence intervals without context_tags."""
    if job_id:
        return [job_id]

    rows = _db.fetchall(
        """
        SELECT DISTINCT j.job_id
          FROM job_history j
          JOIN analysis_audio aa ON aa.job_id = j.job_id
         WHERE j.status IN ('complete', 'deciding', 'persisting')
           AND (aa.context_tags IS NULL OR aa.context_tags = ARRAY[]::TEXT[])
         ORDER BY j.job_id
        """ + (f" LIMIT {int(limit)}" if limit else "")
    )
    return [r["job_id"] for r in rows]


def _get_silence_intervals(job_id: str) -> list[dict]:
    return _db.fetchall(
        """
        SELECT silence_start_sec, silence_end_sec
          FROM analysis_audio
         WHERE job_id = %s
           AND (context_tags IS NULL OR context_tags = ARRAY[]::TEXT[])
         ORDER BY silence_start_sec
        """,
        (job_id,),
    )


def _get_transcript_segments(job_id: str) -> list[dict]:
    return _db.fetchall(
        "SELECT start_sec, end_sec, text FROM analysis_transcript WHERE job_id = %s ORDER BY start_sec",
        (job_id,),
    )


def _get_scene_descriptions(job_id: str) -> dict[int, str]:
    rows = _db.fetchall(
        "SELECT frame_index, scene_description FROM analysis_vision_context WHERE job_id = %s AND scene_description IS NOT NULL",
        (job_id,),
    )
    return {int(r["frame_index"]): r["scene_description"] for r in rows}


def backfill_job(job_id: str) -> int:
    """Generate and store context_tags for one job. Returns number of intervals updated."""
    intervals = _get_silence_intervals(job_id)
    if not intervals:
        logger.info("[%s] No intervals need backfill.", job_id)
        return 0

    transcript_segments = _get_transcript_segments(job_id)
    descriptions = _get_scene_descriptions(job_id)

    updated = 0
    for iv in intervals:
        start = float(iv["silence_start_sec"])
        # Dynamically detect where the current dialogue scene/topic began.
        window_start = dialogue_segmenter.find_context_start(
            transcript_segments=transcript_segments,
            silence_start_sec=start,
        )

        transcript_text = " ".join(
            seg["text"]
            for seg in transcript_segments
            if window_start <= float(seg["start_sec"]) <= start
        ).strip()

        recent_descs = [
            desc
            for frame_idx, desc in sorted(descriptions.items())
            if window_start <= float(frame_idx) <= start and desc
        ]

        tags: list = []
        narrative: str = ""

        try:
            tags = vision_qwen.analyse_silence_context(
                transcript_before=transcript_text,
                scene_descriptions=recent_descs,
                silence_start_sec=start,
            )
        except Exception as exc:
            logger.warning("[%s] Tags failed at %.1fs: %s", job_id, start, exc)

        try:
            narrative = vision_qwen.analyse_context_narrative(
                transcript_before=transcript_text,
                scene_descriptions=recent_descs,
                silence_start_sec=start,
            )
        except Exception as exc:
            logger.warning("[%s] Narrative failed at %.1fs: %s", job_id, start, exc)

        if tags or narrative:
            _db.execute(
                """
                UPDATE analysis_audio
                   SET context_tags    = %s,
                       context_summary = %s
                 WHERE job_id = %s
                   AND silence_start_sec = %s
                """,
                (tags if tags else None, narrative if narrative else None, job_id, start),
            )
            updated += 1
            logger.info("[%s] @%.1fs tags=%s narrative='%s'", job_id, start, tags, narrative[:60])

    logger.info("[%s] Backfill complete: %d/%d intervals updated.", job_id, updated, len(intervals))
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill context_tags for existing jobs")
    parser.add_argument("--job", help="Specific job_id to backfill")
    parser.add_argument("--limit", type=int, help="Max number of jobs to process")
    args = parser.parse_args()

    job_ids = _get_jobs_needing_context(args.job, args.limit)
    if not job_ids:
        logger.info("No jobs need backfill.")
        return

    logger.info("Backfilling %d job(s) ...", len(job_ids))
    total_updated = 0
    for jid in job_ids:
        total_updated += backfill_job(jid)

    logger.info("All done. Total intervals updated: %d", total_updated)


if __name__ == "__main__":
    main()
