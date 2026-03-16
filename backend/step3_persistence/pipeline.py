"""
Step 3 — Ad-Matching Persistence Pipeline
───────────────────────────────────────────
v2.0  : 기본 silence × ad Cartesian Product
v2.5  : target_narrative 후보 포함
v2.6  : Scene-driven 전환 — analysis_audio(침묵) 대신 analysis_scene(씬)을 후보 기준으로 사용

Consumes from QUEUE_STEP3.

For every scene in analysis_scene for this job:
  1. Cross with all ad_inventory entries.
  2. Build candidate list: (scene, ad) pairs with context_narrative.
  3. Pass as JSON payload to QUEUE_STEP4 for scoring.

No additional DB writes at this step.

Run:
    python -m step3_persistence.pipeline
"""

import logging
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import config, db as _db, rabbitmq as mq
from common.logging_setup import setup_logging

setup_logging("step3")
logger = logging.getLogger(__name__)


def _update_job_status(job_id: str, status: str, error: str | None = None) -> None:
    _db.execute(
        "UPDATE job_history SET status=%s, error_message=%s, updated_at=NOW() WHERE job_id=%s",
        (status, error, job_id),
    )


def _get_scene_intervals(job_id: str) -> list[dict]:
    """analysis_scene 테이블에서 씬 목록을 조회한다."""
    return _db.fetchall(
        """
        SELECT id,
               scene_start_sec,
               scene_end_sec,
               (scene_end_sec - scene_start_sec)  AS scene_duration,
               context_narrative
          FROM analysis_scene
         WHERE job_id = %s
         ORDER BY scene_start_sec
        """,
        (job_id,),
    )


def _get_ad_inventory() -> list[dict]:
    return _db.fetchall(
        """
        SELECT ad_id, ad_name, ad_type, resource_path,
               duration_sec, target_narrative, width, height,
               ad_category, ad_category_path
          FROM ad_inventory
        """,
    )


def build_candidates(job_id: str) -> list[dict]:
    """
    Return a list of candidate dicts, each representing one
    (scene × ad) pair to be scored.

    Schema (v2.10):
    {
        "scene_start_sec":   float,
        "scene_end_sec":     float,
        "scene_duration":    float,
        "context_narrative": str,
        "ad_id":             str,
        "ad_name":           str,
        "ad_type":           str,
        "ad_duration_sec":   float | None,
        "target_narrative":  str,
        "ad_category":       str,        -- NULL이면 "" (카테고리 보너스 미적용)
        "ad_category_path":  list[str],  -- NULL이면 []
    }
    """
    scenes     = _get_scene_intervals(job_id)
    ad_entries = _get_ad_inventory()

    if not scenes:
        logger.warning("[%s] No scenes found in analysis_scene — no candidates.", job_id)
        return []

    if not ad_entries:
        logger.warning("[%s] Ad inventory is empty — no candidates.", job_id)
        return []

    candidates = []
    for scene in scenes:
        for ad in ad_entries:
            candidates.append({
                "scene_start_sec":   float(scene["scene_start_sec"]),
                "scene_end_sec":     float(scene["scene_end_sec"]),
                "scene_duration":    float(scene["scene_duration"]),
                "context_narrative": scene.get("context_narrative") or "",
                "ad_id":             ad["ad_id"],
                "ad_name":           ad.get("ad_name") or "",
                "ad_type":           ad["ad_type"],
                "ad_duration_sec":   float(ad["duration_sec"]) if ad["duration_sec"] is not None else None,
                "target_narrative":  ad.get("target_narrative") or "",
                "ad_category":       ad.get("ad_category") or "",
                "ad_category_path":  ad.get("ad_category_path") or [],
            })

    logger.info(
        "[%s] Built %d candidate pairs (%d scenes × %d ads)",
        job_id, len(candidates), len(scenes), len(ad_entries),
    )
    return candidates


def run(job_id: str) -> None:
    _update_job_status(job_id, "persisting")
    try:
        candidates = build_candidates(job_id)
        _update_job_status(job_id, "deciding")
        mq.publish(config.QUEUE_STEP4, {"job_id": job_id, "candidates": candidates})
        logger.info("[%s] Step-3 complete → published %d candidates", job_id, len(candidates))
    except Exception as exc:
        _update_job_status(job_id, "failed", str(exc))
        logger.exception("[%s] Step-3 failed: %s", job_id, exc)
        raise


def _on_message(payload: dict) -> None:
    run(payload["job_id"])


if __name__ == "__main__":
    mq.consume(config.QUEUE_STEP3, _on_message)
