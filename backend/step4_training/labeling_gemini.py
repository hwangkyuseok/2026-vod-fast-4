"""
step4_training/labeling_gemini.py — Cross-Encoder 학습 데이터 라벨링
──────────────────────────────────────────────────────────────────────
DB의 (씬, 광고) 쌍을 Gemini로 평가하여 cross_encoder_labels 테이블에 저장.

v2.0 변경사항:
  - FROM analysis_scene_final → analysis_scene (desire 있는 씬만)
  - generate_scene_narrative.py 단계 불필요 (step2에서 context_narrative + desire 직접 생성)
  - 프롬프트: 시각적 맥락 적합도 → 소비 욕구 연결성 평가로 변경
    (target_narrative가 소비 욕구 형식으로 바뀌었으므로 평가 기준 통일)

라벨 기준:
  >= 0.7        → positive   (Positive 정답 데이터)
  0.3 < x < 0.7 → ambiguous  (학습 제외, DB에는 저장)
  <= 0.3        → negative   (Negative 정답 데이터)

실행:
    python -m step4_training.labeling_gemini [--ads-per-scene N] [--limit N] [--dry-run] [--force]

환경변수:
    GEMINI_API_KEY : Google AI Studio API 키
"""

import argparse
import logging
import random
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
_RPM_INTERVAL  = 0.1

LABEL_POSITIVE   = "positive"   # score >= 0.7
LABEL_AMBIGUOUS  = "ambiguous"  # 0.3 < score < 0.7
LABEL_NEGATIVE   = "negative"   # score <= 0.3

# ── 라벨링 프롬프트 (v2.0 — 소비 욕구 연결성 평가) ───────────────────────────
# target_narrative가 "시청자의 소비 욕구" 형식으로 바뀌었으므로
# 씬 desire와 광고 욕구의 연결성을 평가하도록 프롬프트 변경
_PROMPT_TEMPLATE = (
    "아래는 TV 드라마 씬 분석과, 이 씬에 삽입될 광고의 소비 욕구 설명입니다.\n\n"
    "【씬 분석 (상황/감정/욕구)】\n{context_narrative}\n\n"
    "【광고가 시청자에게 자극하는 소비 욕구】\n{target_narrative}\n\n"
    "씬을 본 시청자의 욕구(씬 분석의 '욕구' 항목)와 "
    "광고가 자극하는 소비 욕구가 얼마나 자연스럽게 연결되는지 평가하세요.\n\n"
    "평가 기준:\n"
    "1.0 = 씬에서 생긴 욕구를 광고가 완벽히 충족 (예: 금융 씬 + 금융 광고)\n"
    "0.5 = 간접적으로 연결 가능하지만 억지스러움\n"
    "0.0 = 씬의 욕구와 광고 욕구가 전혀 관련 없음 (예: 액션 씬 + 요리 광고)\n\n"
    "주의: 시각적 분위기 유사성이 아닌 소비 욕구의 연결성으로만 평가하세요.\n"
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
    if score >= 0.7:
        return LABEL_POSITIVE
    elif score <= 0.3:
        return LABEL_NEGATIVE
    return LABEL_AMBIGUOUS  # 0.3 < score < 0.7


# ── DB 조회 ────────────────────────────────────────────────────────────────────

def _get_pairs(limit: int | None, force: bool, ads_per_scene: int | None = None) -> list[dict]:
    """
    라벨링할 (씬, 광고) 쌍 조회.
    v2.0: analysis_scene_final → analysis_scene (desire + context_narrative 있는 씬만)
    force=False이면 아직 라벨링되지 않은 쌍만 반환.
    ads_per_scene이 지정되면 씬당 랜덤 샘플링.
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
               AND s.desire            IS NOT NULL AND s.desire            <> ''
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
               AND s.desire            IS NOT NULL AND s.desire            <> ''
               AND a.target_narrative  IS NOT NULL AND a.target_narrative  <> ''
               AND NOT EXISTS (
                   SELECT 1 FROM cross_encoder_labels c
                    WHERE c.scene_id = s.id AND c.ad_id = a.ad_id
               )
             ORDER BY s.id, a.ad_id
        """

    rows = _db.fetchall(sql)

    if ads_per_scene:
        # 씬별로 그룹핑 후 랜덤 샘플링
        from collections import defaultdict
        by_scene: dict[int, list[dict]] = defaultdict(list)
        for row in rows:
            by_scene[row["scene_id"]].append(row)
        sampled = []
        for scene_rows in by_scene.values():
            sampled.extend(random.sample(scene_rows, min(ads_per_scene, len(scene_rows))))
        random.shuffle(sampled)
        rows = sampled

    if limit:
        rows = rows[:int(limit)]

    return rows


def _save_label(scene_id: int, ad_id: str, context: str, target: str, score: float, label: str) -> None:
    for attempt in range(5):
        try:
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
            return
        except Exception as exc:
            logger.warning("DB save failed (attempt %d/5): %s", attempt + 1, exc)
            time.sleep(5 * (attempt + 1))


# ── 메인 ──────────────────────────────────────────────────────────────────────

def run(limit: int | None = None, dry_run: bool = False, force: bool = False, ads_per_scene: int | None = None) -> None:
    pairs = _get_pairs(limit, force, ads_per_scene)
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
    parser.add_argument("--ads-per-scene", type=int,  default=None, help="씬당 랜덤 샘플링할 광고 수 (기본: 전체)")
    parser.add_argument("--limit",         type=int,  default=None)
    parser.add_argument("--dry-run",       action="store_true")
    parser.add_argument("--force",         action="store_true", help="이미 라벨링된 쌍도 덮어쓰기")
    args = parser.parse_args()

    run(limit=args.limit, dry_run=args.dry_run, force=args.force, ads_per_scene=args.ads_per_scene)
