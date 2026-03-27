"""
step4_training/generate_scene_narrative.py — 씬 Narrative V2 백필 (Gemini)
──────────────────────────────────────────────────────────────────────────
analysis_scene의 기존 씬들을 새 프롬프트(상황/감정/욕구)로 Gemini 재분석하여
analysis_scene_narrative_v2 테이블에 저장.

Cross-Encoder 학습 데이터 생성 전 선행 작업.

실행:
    python -m step4_training.generate_scene_narrative [--limit N] [--dry-run] [--force]

환경변수:
    GEMINI_API_KEY : Google AI Studio API 키
"""

import argparse
import logging
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from google import genai
from common import config, db as _db
from common.logging_setup import setup_logging

setup_logging("generate_scene_narrative")
logger = logging.getLogger(__name__)

# ── Gemini 설정 ────────────────────────────────────────────────────────────────
GEMINI_API_KEY = getattr(config, "GEMINI_API_KEY", "")
_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
]
_model_index = 0
_RPM_INTERVAL = 1.0
_client = None
_last_call_time: float = 0.0

# ── 새 프롬프트 (상황/감정/욕구 구조화) ──────────────────────────────────────
_PROMPT_TEMPLATE = (
    "아래는 한국 TV 드라마의 한 씬에 대한 정보입니다.\n\n"
    "{context_block}\n\n"
    "이 씬을 분석하여 아래 세 항목을 각각 정확히 1문장으로 작성하세요.\n"
    "반드시 'label: 내용' 형식을 지켜야 합니다.\n\n"
    "상황: 이 씬에 등장하는 장면, 배경, 인물의 행동을 묘사하세요. (감정·느낌 표현 금지)\n"
    "감정: 이 씬이 시청자에게 전달하는 감성적 분위기나 정서를 표현하세요. (상황 묘사 금지)\n"
    "욕구: 이 씬이 타겟하는 시청자의 내면적 니즈나 욕구를 서술하세요. (감정 단어·상황 묘사 금지)\n\n"
    "예시:\n"
    "상황: 퇴근 후 집에 돌아온 직장인이 소파에 앉아 따뜻한 음료를 마시는 장면이다.\n"
    "감정: 하루의 피로가 녹아드는 포근하고 안도감 있는 분위기를 전달한다.\n"
    "욕구: 바쁜 일상 속에서 잠깐의 휴식과 자신을 위한 작은 여유를 원하는 사람에게 어울린다.\n\n"
    "반드시 한국어로만 작성하고, 위 예시처럼 세 줄로만 답하세요."
)


# ── Gemini 클라이언트 ──────────────────────────────────────────────────────────

def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")
        _client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("Gemini client initialised.")
    return _client


def _rate_limit() -> None:
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < _RPM_INTERVAL:
        time.sleep(_RPM_INTERVAL - elapsed)
    _last_call_time = time.time()


def _call_gemini(prompt: str, max_retries: int = 5) -> str:
    global _model_index
    client = _get_client()

    for attempt in range(max_retries):
        _rate_limit()
        current_model = _MODELS[_model_index % len(_MODELS)]
        _model_index += 1

        try:
            response = client.models.generate_content(
                model=current_model,
                contents=[prompt],
            )
            return (response.text or "").strip()
        except Exception as exc:
            if "429" in str(exc) or "quota" in str(exc).lower():
                logger.warning("Rate limit on %s. Switching model... (%d/%d)", current_model, attempt + 1, max_retries)
                time.sleep(1.5)
            else:
                logger.warning("Gemini error on %s (%d/%d): %s", current_model, attempt + 1, max_retries, exc)
                if attempt == max_retries - 1:
                    return ""
    return ""


# ── DB 조회 ────────────────────────────────────────────────────────────────────

def _get_scenes(limit: int | None, force: bool) -> list[dict]:
    if force:
        sql = """
            SELECT s.id AS scene_id,
                   s.job_id,
                   s.scene_start_sec,
                   s.scene_end_sec
              FROM analysis_scene s
             ORDER BY s.id
        """
    else:
        sql = """
            SELECT s.id AS scene_id,
                   s.job_id,
                   s.scene_start_sec,
                   s.scene_end_sec
              FROM analysis_scene s
             WHERE NOT EXISTS (
                 SELECT 1 FROM analysis_scene_narrative_v2 v
                  WHERE v.scene_id = s.id
             )
             ORDER BY s.id
        """

    if limit:
        sql += f" LIMIT {int(limit)}"

    return _db.fetchall(sql)


def _get_transcript(job_id: str, scene_start_sec: float, scene_end_sec: float) -> str:
    rows = _db.fetchall(
        """
        SELECT text FROM analysis_transcript
         WHERE job_id = %s
           AND start_sec >= %s AND start_sec < %s
         ORDER BY start_sec
        """,
        (job_id, scene_start_sec, scene_end_sec),
    )
    return " ".join(r["text"] for r in rows if r.get("text")).strip()


def _save_narrative(scene_id: int, job_id: str, scene_start_sec: float, scene_end_sec: float, narrative: str) -> None:
    _db.execute(
        """
        INSERT INTO analysis_scene_narrative_v2
            (scene_id, job_id, scene_start_sec, scene_end_sec, context_narrative)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (scene_id) DO UPDATE
            SET context_narrative = EXCLUDED.context_narrative
        """,
        (scene_id, job_id, scene_start_sec, scene_end_sec, narrative),
    )


# ── 메인 ──────────────────────────────────────────────────────────────────────

def run(limit: int | None = None, dry_run: bool = False, force: bool = False) -> None:
    scenes = _get_scenes(limit, force)
    total = len(scenes)
    logger.info("Found %d scene(s) to process (force=%s, dry_run=%s)", total, force, dry_run)

    if dry_run:
        for s in scenes:
            print(f"[DRY-RUN] scene_id={s['scene_id']}  job_id={s['job_id']}  "
                  f"[{s['scene_start_sec']:.1f}-{s['scene_end_sec']:.1f}s]")
        return

    success = skipped = 0

    for i, scene in enumerate(scenes, 1):
        scene_id = scene["scene_id"]
        job_id   = scene["job_id"]
        s_start  = float(scene["scene_start_sec"])
        s_end    = float(scene["scene_end_sec"])

        logger.info("[%d/%d] scene_id=%d  job=%s  [%.1f-%.1fs]",
                    i, total, scene_id, job_id, s_start, s_end)

        transcript = _get_transcript(job_id, s_start, s_end)
        context_block = f"대사: {transcript}" if transcript else "(대사 없음)"

        prompt = _PROMPT_TEMPLATE.format(context_block=context_block)
        narrative = _call_gemini(prompt)

        if not narrative:
            logger.warning("  Empty response — skipping scene_id=%d", scene_id)
            skipped += 1
            continue

        _save_narrative(scene_id, job_id, s_start, s_end, narrative)
        logger.info("  saved (%d chars): %s", len(narrative), narrative[:80])
        success += 1

    logger.info("Done. success=%d, skipped=%d / total=%d", success, skipped, total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scene Narrative V2 Backfill (Gemini)")
    parser.add_argument("--limit",   type=int,  default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force",   action="store_true", help="이미 생성된 narrative도 덮어쓰기")
    args = parser.parse_args()

    run(limit=args.limit, dry_run=args.dry_run, force=args.force)
