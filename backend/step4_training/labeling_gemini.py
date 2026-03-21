"""
step4_training/labeling_gemini.py — Cross-Encoder 학습 데이터 라벨링
──────────────────────────────────────────────────────────────────────
DB의 (씬, 광고) 쌍을 Gemini로 평가하여 cross_encoder_labels 테이블에 저장.

라벨 기준:
  > 0.9   → train        (학습 데이터로 바로 사용)
  0.4~0.9 → review       (검토 후 사용)
  < 0.4   → human_check  (사람 확인 필요)

실행:
    python -m step4_training.labeling_gemini [--limit N] [--dry-run] [--force]

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
from google.genai import types
from common import config, db as _db
from common.logging_setup import setup_logging

setup_logging("labeling_gemini")
logger = logging.getLogger(__name__)

# ── 설정 ──────────────────────────────────────────────────────────────────────
_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
]
_model_index = 0
GEMINI_API_KEY = getattr(config, "GEMINI_API_KEY", "")
_RPM_INTERVAL  = 1.0

LABEL_TRAIN        = "train"        # score > 0.9
LABEL_REVIEW       = "review"       # 0.4 <= score <= 0.9
LABEL_HUMAN_CHECK  = "human_check"  # score < 0.4

# ── 라벨링 프롬프트 ────────────────────────────────────────────────────────────
_PROMPT_TEMPLATE = (
    "아래 두 텍스트를 읽고, 광고가 해당 영상 씬에 얼마나 잘 어울리는지 평가하세요.\n\n"
    "【씬 설명】\n{context_narrative}\n\n"
    "【광고 설명】\n{target_narrative}\n\n"
    "위 씬에 이 광고를 삽입했을 때의 맥락 적합도를 0.0~1.0 사이의 숫자 하나로만 답하세요.\n"
    "1.0 = 완벽하게 어울림, 0.0 = 전혀 어울리지 않음\n"
    "숫자만 답하세요. 설명 금지."
)

# ── Gemini 클라이언트 ──────────────────────────────────────────────────────────
_client = None
_last_call_time: float = 0.0


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


def _parse_score(raw: str) -> float | None:
    """Gemini 응답에서 0.0~1.0 점수 파싱. 실패 시 None."""
    try:
        score = float(raw.strip())
        if 0.0 <= score <= 1.0:
            return score
    except ValueError:
        pass
    logger.warning("Failed to parse score from Gemini response: %r", raw)
    return None


def _assign_label(score: float) -> str:
    if score > 0.9:
        return LABEL_TRAIN
    elif score >= 0.4:
        return LABEL_REVIEW
    else:
        return LABEL_HUMAN_CHECK


# ── DB 조회 ────────────────────────────────────────────────────────────────────

def _get_pairs(limit: int | None, force: bool) -> list[dict]:
    """
    라벨링할 (씬, 광고) 쌍 조회.
    force=False이면 아직 라벨링되지 않은 쌍만 반환.
    """
    if force:
        sql = """
            SELECT s.id AS scene_id,
                   s.context_narrative,
                   a.ad_id,
                   a.target_narrative
              FROM analysis_scene s
              JOIN ad_inventory a ON TRUE
             WHERE s.context_narrative IS NOT NULL AND s.context_narrative <> ''
               AND a.target_narrative  IS NOT NULL AND a.target_narrative  <> ''
             ORDER BY s.id, a.ad_id
        """
    else:
        sql = """
            SELECT s.id AS scene_id,
                   s.context_narrative,
                   a.ad_id,
                   a.target_narrative
              FROM analysis_scene s
              JOIN ad_inventory a ON TRUE
             WHERE s.context_narrative IS NOT NULL AND s.context_narrative <> ''
               AND a.target_narrative  IS NOT NULL AND a.target_narrative  <> ''
               AND NOT EXISTS (
                   SELECT 1 FROM cross_encoder_labels c
                    WHERE c.scene_id = s.id AND c.ad_id = a.ad_id
               )
             ORDER BY s.id, a.ad_id
        """

    if limit:
        sql += f" LIMIT {int(limit)}"

    return _db.fetchall(sql)


def _save_label(scene_id: int, ad_id: str, context: str, target: str, score: float, label: str) -> None:
    _db.execute(
        """
        INSERT INTO cross_encoder_labels
            (scene_id, ad_id, context_narrative, target_narrative, gemini_score, label)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (scene_id, ad_id) DO UPDATE
            SET gemini_score = EXCLUDED.gemini_score,
                label        = EXCLUDED.label
        """,
        (scene_id, ad_id, context, target, score, label),
    )


# ── 메인 ──────────────────────────────────────────────────────────────────────

def run(limit: int | None = None, dry_run: bool = False, force: bool = False) -> None:
    pairs = _get_pairs(limit, force)
    total = len(pairs)
    logger.info("Found %d pair(s) to label (force=%s, dry_run=%s)", total, force, dry_run)

    if dry_run:
        for p in pairs:
            print(f"[DRY-RUN] scene_id={p['scene_id']}  ad_id={p['ad_id']}")
        return

    success = skipped = 0

    for i, p in enumerate(pairs, 1):
        prompt = _PROMPT_TEMPLATE.format(
            context_narrative=p["context_narrative"],
            target_narrative=p["target_narrative"],
        )

        logger.info("[%d/%d] scene=%s  ad=%s", i, total, p["scene_id"], p["ad_id"])
        raw = _call_gemini(prompt)
        score = _parse_score(raw)

        if score is None:
            logger.warning("[%d/%d] Skip — invalid response: %r", i, total, raw)
            skipped += 1
            continue

        label = _assign_label(score)
        _save_label(p["scene_id"], p["ad_id"], p["context_narrative"], p["target_narrative"], score, label)
        logger.info("  score=%.3f  label=%s", score, label)
        success += 1

    logger.info("Done. success=%d, skipped=%d / total=%d", success, skipped, total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gemini Cross-Encoder Label Generator")
    parser.add_argument("--limit",   type=int,  default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force",   action="store_true", help="이미 라벨링된 쌍도 덮어쓰기")
    args = parser.parse_args()

    run(limit=args.limit, dry_run=args.dry_run, force=args.force)
