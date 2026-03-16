"""
Step 5 — FastAPI REST Server
─────────────────────────────
Endpoints:
  POST /jobs                  → submit a local video path, start pipeline
  GET  /jobs/{job_id}         → job status
  GET  /overlay/{job_id}      → VOD overlay metadata JSON (Step-5 output)

Static file serving:
  /media/jobs/{job_id}/...    → extracted frames & audio
  /media/ads/videos/...       → ad video clips
  /media/ads/images/...       → ad banner images

Run:
    python -m step5_api.server
  or
    uvicorn step5_api.server:app --host 0.0.0.0 --port 8000
"""

import logging
import os
import uuid
from pathlib import Path
from urllib.parse import quote

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from common import config, db as _db, rabbitmq as mq
from common.logging_setup import setup_logging

setup_logging("step5_api")
logger = logging.getLogger(__name__)

app = FastAPI(title="VOD Ad Overlay API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Static file mounts ───────────────────────────────────────────────────────

_jobs_storage = Path(config.STORAGE_BASE) / "jobs"
_jobs_storage.mkdir(parents=True, exist_ok=True)

app.mount(
    "/media/jobs",
    StaticFiles(directory=str(_jobs_storage)),
    name="jobs-media",
)
app.mount(
    "/media/ads/videos",
    StaticFiles(directory=config.AD_VIDEO_DIR),
    name="ad-videos",
)
app.mount(
    "/media/ads/images",
    StaticFiles(directory=config.AD_IMAGE_DIR),
    name="ad-images",
)

_vod_dir = config.VOD_DIR   # Windows: D:\...\vod  /  Linux·Docker: /vod

# ─── Request / Response models ────────────────────────────────────────────────

class JobSubmitRequest(BaseModel):
    video_path: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    input_video_path: str
    error_message: str | None = None
    created_at: str
    updated_at: str


class FeedbackRequest(BaseModel):
    label: int = Field(..., description="-1=부적합, 0=보통, 1=적합")
    source: str = Field(default="user", description="'user' | 'auto'")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _ad_url(ad: dict) -> str:
    """Return a fully-qualified URL for the ad resource."""
    resource_path = ad["resource_path"]
    ad_type = ad["ad_type"]
    # replace("\\", "/") 후 split으로 파일명 추출 — Windows 경로(D:\...)도 처리
    filename = resource_path.replace("\\", "/").split("/")[-1]
    if ad_type == "video_clip":
        return f"{config.API_BASE_URL}/media/ads/videos/{filename}"
    return f"{config.API_BASE_URL}/media/ads/images/{filename}"


# ─── Routes ──────────────────────────────────────────────────────────────────

_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".m4v"}

@app.get("/vod/files")
def list_vod_files():
    """VOD_DIR에 있는 재생 가능한 영상 파일 목록 반환."""
    vod_dir = Path(config.VOD_DIR)
    if not vod_dir.is_dir():
        return {"files": [], "vod_dir": str(vod_dir)}
    files = [
        {"name": f.name, "path": str(f)}
        for f in sorted(vod_dir.iterdir())
        if f.is_file() and f.suffix.lower() in _VIDEO_EXTS
    ]
    return {"files": files, "vod_dir": str(vod_dir)}


@app.post("/jobs", status_code=202)
def submit_job(body: JobSubmitRequest):
    """Submit a local video for processing. Returns job_id."""
    if not os.path.isfile(body.video_path):
        raise HTTPException(status_code=400, detail=f"File not found: {body.video_path}")

    job_id = str(uuid.uuid4())
    _db.execute(
        """
        INSERT INTO job_history (job_id, status, input_video_path)
        VALUES (%s, 'pending', %s)
        """,
        (job_id, body.video_path),
    )

    # Trigger Step-1 via RabbitMQ
    mq.publish(
        config.QUEUE_STEP1,
        {"job_id": job_id, "video_path": body.video_path},
    )
    logger.info("Job %s submitted for %s", job_id, body.video_path)
    return {"job_id": job_id, "status": "pending"}


@app.get("/jobs/completed")
def list_completed_jobs():
    """완료된 job 목록 반환 (드롭다운용). 최신 순 정렬."""
    rows = _db.fetchall(
        """
        SELECT job_id, input_video_path, updated_at
          FROM job_history
         WHERE status = 'complete'
         ORDER BY updated_at DESC
        """,
    )
    return {
        "jobs": [
            {
                "job_id":     str(r["job_id"]),
                "filename":   r["input_video_path"].replace("\\", "/").split("/")[-1],
                "updated_at": r["updated_at"].isoformat(),
            }
            for r in rows
        ]
    }


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str):
    row = _db.fetchone(
        "SELECT * FROM job_history WHERE job_id = %s",
        (job_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id":            str(row["job_id"]),
        "status":            row["status"],
        "input_video_path":  row["input_video_path"],
        "error_message":     row["error_message"],
        "created_at":        row["created_at"].isoformat(),
        "updated_at":        row["updated_at"].isoformat(),
    }


@app.get("/overlay/{job_id}")
def get_overlay_metadata(job_id: str):
    """
    Return the VOD overlay metadata JSON for a completed job.

    Response schema:
    {
        "job_id":             str,
        "original_video_url": str,
        "total_duration_sec": float,
        "overlays": [
            {
                "matched_ad_id":         str,
                "ad_resource_url":       str,
                "ad_type":               str,
                "overlay_start_time_sec": float,
                "overlay_duration_sec":  float,
                "coordinates_x":         int,
                "coordinates_y":         int,
                "coordinates_w":         int,
                "coordinates_h":         int,
                "score":                 int,
            },
            ...
        ]
    }
    """
    # Validate job
    job = _db.fetchone("SELECT * FROM job_history WHERE job_id = %s", (job_id,))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "complete":
        raise HTTPException(
            status_code=202,
            detail=f"Job is not complete yet. Current status: {job['status']}",
        )

    # Preprocessing info (for video URL + duration)
    preproc = _db.fetchone(
        "SELECT * FROM video_preprocessing_info WHERE job_id = %s",
        (job_id,),
    )
    if preproc is None:
        raise HTTPException(status_code=500, detail="Preprocessing info missing")

    original_video_path = preproc["original_video_path"]
    # DB may store Windows paths (D:\...) — split on both \ and / to get just the filename
    original_video_filename = original_video_path.replace("\\", "/").split("/")[-1]

    # Decision results joined with ad inventory
    results = _db.fetchall(
        """
        SELECT dr.id AS decision_id,
               dr.ad_id,
               dr.overlay_start_time_sec,
               dr.overlay_duration_sec,
               dr.coordinates_x,
               dr.coordinates_y,
               dr.coordinates_w,
               dr.coordinates_h,
               dr.score,
               ai.ad_type,
               ai.resource_path
          FROM decision_result dr
          JOIN ad_inventory ai ON ai.ad_id = dr.ad_id
         WHERE dr.job_id = %s
         ORDER BY dr.overlay_start_time_sec, dr.score DESC
        """,
        (job_id,),
    )

    # ── Deduplicate & remove overlapping windows ──────────────────────────────
    # Step 1: per unique overlay_start_time_sec, keep the highest-scoring row.
    #         (rows are already sorted by start_time ASC, score DESC so the
    #          first occurrence per start time is always the best one.)
    seen_starts: set[float] = set()
    deduped: list[dict] = []
    for r in results:
        ts = float(r["overlay_start_time_sec"])
        if ts not in seen_starts:
            seen_starts.add(ts)
            deduped.append(r)

    # Step 2: greedy overlap removal — if overlay[i] starts before overlay[i-1]
    #         ends, discard it (the earlier one is already the best for its slot).
    non_overlapping: list[dict] = []
    last_end = -1.0
    for r in deduped:
        start = float(r["overlay_start_time_sec"])
        end   = start + float(r["overlay_duration_sec"])
        if start >= last_end:
            non_overlapping.append(r)
            last_end = end
        # else: overlapping with the previous overlay → skip

    overlays = [
        {
            "decision_id":            int(r["decision_id"]),  # 피드백 제출에 사용
            "matched_ad_id":          r["ad_id"],
            "ad_resource_url":        _ad_url(r),
            "ad_type":                r["ad_type"],
            "overlay_start_time_sec": float(r["overlay_start_time_sec"]),
            "overlay_duration_sec":   float(r["overlay_duration_sec"]),
            "coordinates_x":          r["coordinates_x"],
            "coordinates_y":          r["coordinates_y"],
            "coordinates_w":          r["coordinates_w"],
            "coordinates_h":          r["coordinates_h"],
            "score":                  int(r["score"]),
        }
        for r in non_overlapping
    ]

    # Serve the original video through the /media/jobs/{job_id}/... mount;
    # but the original file may live anywhere. We expose it via a dedicated route.
    original_video_url = (
        f"{config.API_BASE_URL}/media/source/{quote(original_video_filename)}"
    )

    return {
        "job_id":             job_id,
        "original_video_url": original_video_url,
        "total_duration_sec": float(preproc["duration_sec"]),
        "overlays":           overlays,
    }


# ─── Feedback (레이블 수집) ───────────────────────────────────────────────────

@app.post("/feedback/{decision_id}", status_code=201)
def submit_feedback(decision_id: int, body: FeedbackRequest):
    """
    광고 배치에 대한 적합성 피드백을 기록한다.
    프론트엔드의 👍/👎 버튼 또는 자동 평가 스크립트에서 호출.

    label: -1=부적합, 0=보통, 1=적합
    """
    if body.label not in (-1, 0, 1):
        raise HTTPException(status_code=422, detail="label must be -1, 0, or 1")

    # decision_id 존재 확인
    row = _db.fetchone("SELECT id FROM decision_result WHERE id = %s", (decision_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="decision_id not found")

    # 중복 제출 시 업데이트 (UPSERT)
    _db.execute(
        """
        INSERT INTO ad_placement_feedback (decision_id, label, source)
        VALUES (%s, %s, %s)
        ON CONFLICT (decision_id) DO UPDATE
            SET label = EXCLUDED.label,
                source = EXCLUDED.source,
                created_at = NOW()
        """,
        (decision_id, body.label, body.source),
    )
    logger.info("Feedback recorded: decision_id=%d label=%d source=%s",
                decision_id, body.label, body.source)
    return {"decision_id": decision_id, "label": body.label}


# ─── Source video serving ─────────────────────────────────────────────────────
# StaticFiles does not handle non-ASCII filenames reliably in all Starlette
# versions, so we use a regular route + FileResponse instead.
# FileResponse supports Range requests (HTTP 206) natively via Starlette.

from fastapi.responses import FileResponse

@app.get("/media/source/{filename:path}")
def serve_source_video(filename: str):
    """Stream the original source VOD by filename (supports Range / seek)."""
    from urllib.parse import unquote
    decoded = unquote(filename)          # handle %EB%AC%B4… → 무명전설…
    path = Path(_vod_dir) / decoded
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Source video not found")
    return FileResponse(str(path), media_type="video/mp4")


# ─── Dev entry-point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "step5_api.server:app",
        host=config.API_HOST,
        port=config.API_PORT,
        reload=False,
    )
