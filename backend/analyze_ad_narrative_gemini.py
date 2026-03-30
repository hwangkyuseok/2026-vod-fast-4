"""
analyze_ad_narrative_gemini.py — Ad Narrative 생성 (Gemini Flash 버전)
──────────────────────────────────────────────────────────────────────
analyze_ad_narrative.py (Qwen2-VL)과 동일한 로직을 Gemini Flash API로 대체.

실행:
    python analyze_ad_narrative_gemini.py [--limit N] [--dry-run] [--force]

환경변수:
    GEMINI_API_KEY       : Google AI Studio API 키
    GEMINI_MODEL         : 모델명 (기본: gemini-2.0-flash)
    GEMINI_RPM_INTERVAL  : API 호출 간 최소 대기 초 (기본: 0.1 — 유료 티어)
                           무료 티어(15 RPM) 사용 시 4.0 으로 설정

변경사항 (v3.0):
    - 개선 1: target_narrative 프롬프트를 소비 욕구(욕구) 형식으로 변경
      (씬 desire 필드와 임베딩 유사도 비교를 위해 형식 통일)
    - GEMINI_RPM_INTERVAL config 연동 (하드코딩 4.0 → 동적 설정)

변경사항 (v2.0):
    - google.generativeai (deprecated) → google.genai (신규 SDK) 교체
"""

import argparse
import logging
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))

from google import genai
from google.genai import types
from common import config, db as _db
from common.logging_setup import setup_logging

setup_logging("analyze_ad_narrative_gemini")
logger = logging.getLogger(__name__)

# ── 설정 ────────────────────────────────────────────────────────────────────────
_MODEL_NAME   = getattr(config, "GEMINI_MODEL", "gemini-2.0-flash")
_API_KEY      = getattr(config, "GEMINI_API_KEY", "")
# 호출 간 최소 대기 시간: config → 환경변수 GEMINI_RPM_INTERVAL
# 무료 티어 15 RPM → 4.0s / 유료 티어 1000 RPM → 0.1s (기본값)
_RPM_INTERVAL = float(getattr(config, "GEMINI_RPM_INTERVAL", 0.1))

AD_VIDEO_DIR = getattr(config, "AD_VIDEO_DIR", "")
AD_IMAGE_DIR = getattr(config, "AD_IMAGE_DIR", "")

# ── Narrative 프롬프트 (v3.0 — 소비 욕구 형식) ────────────────────────────────
# 씬 desire 필드("이 씬을 본 시청자가 느끼는 소비 욕구")와 임베딩 비교를 위해
# 광고 target_narrative도 동일한 소비 욕구 형식으로 작성.
AD_NARRATIVE_PROMPT = (
    "이 광고를 분석하여, 이 광고를 본 시청자가 느끼게 되는 소비 욕구를 "
    "한 문장으로 서술하세요.\n\n"
    "규칙:\n"
    "- 구체적인 소비 행동으로 표현하세요 "
    "(예: 음식 구매 욕구, 금융상품 가입 충동, 여행 충동, 뷰티·패션 구매 욕구 등)\n"
    "- 광고 분위기나 시각적 묘사는 금지\n"
    "- 반드시 소비 욕구(구매하고 싶은 마음)를 중심으로 서술하세요\n\n"
    "예시:\n"
    "- KB국민은행 광고: '안정적인 노후를 위해 예적금이나 금융 서비스에 즉시 가입하고 싶어진다.'\n"
    "- 치킨 배달 광고: '바삭한 치킨을 즉시 주문하거나 배달앱을 열어보고 싶어진다.'\n"
    "- 해외여행 광고: '패키지 여행 상품을 검색하거나 여행 예약을 즉시 하고 싶어진다.'\n\n"
    "반드시 한국어로만 작성하고, 한 문장으로 끝내세요."
)

AD_NARRATIVE_PROMPT_WITH_CATEGORY = (
    "이 광고는 '{category}' 카테고리의 광고입니다.\n\n"
    "이 광고를 분석하여, 이 광고를 본 시청자가 느끼게 되는 소비 욕구를 "
    "한 문장으로 서술하세요.\n\n"
    "규칙:\n"
    "- 구체적인 소비 행동으로 표현하세요 "
    "(예: 음식 구매 욕구, 금융상품 가입 충동, 여행 충동, 뷰티·패션 구매 욕구 등)\n"
    "- 광고 분위기나 시각적 묘사는 금지\n"
    "- 반드시 소비 욕구(구매하고 싶은 마음)를 중심으로 서술하세요\n\n"
    "예시:\n"
    "- KB국민은행 광고: '안정적인 노후를 위해 예적금이나 금융 서비스에 즉시 가입하고 싶어진다.'\n"
    "- 치킨 배달 광고: '바삭한 치킨을 즉시 주문하거나 배달앱을 열어보고 싶어진다.'\n"
    "- 해외여행 광고: '패키지 여행 상품을 검색하거나 여행 예약을 즉시 하고 싶어진다.'\n\n"
    "반드시 한국어로만 작성하고, 한 문장으로 끝내세요."
)

# ── Gemini 클라이언트 ──────────────────────────────────────────────────────────
_client: genai.Client | None = None
_last_call_time: float = 0.0


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not _API_KEY:
            raise RuntimeError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")
        _client = genai.Client(api_key=_API_KEY)
        logger.info("Gemini Flash client initialised. model=%s", _MODEL_NAME)
    return _client


def _rate_limit() -> None:
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < _RPM_INTERVAL:
        time.sleep(_RPM_INTERVAL - elapsed)
    _last_call_time = time.time()


def _call_gemini(contents: list, max_retries: int = 3) -> str:
    client = _get_client()
    for attempt in range(max_retries):
        _rate_limit()
        try:
            response = client.models.generate_content(
                model=_MODEL_NAME,
                contents=contents,
            )
            return (response.text or "").strip()
        except Exception as exc:
            if "429" in str(exc) or "quota" in str(exc).lower():
                wait = 60 * (attempt + 1)
                logger.warning("Rate limit — waiting %ds", wait)
                time.sleep(wait)
            else:
                logger.warning(
                    "Gemini error (attempt %d/%d): %s",
                    attempt + 1, max_retries, exc,
                )
                if attempt == max_retries - 1:
                    return ""
    return ""


# ── 유틸리티 ───────────────────────────────────────────────────────────────────

def _resolve_path(resource_path: str, ad_type: str) -> str:
    """DB 경로(Windows/Linux) → 컨테이너 내 실제 경로 변환."""
    if not resource_path:
        return ""
    if len(resource_path) >= 3 and resource_path[1] == ":":
        filename = resource_path.replace("\\", "/").split("/")[-1]
        base_dir = AD_VIDEO_DIR if ad_type == "video_clip" else AD_IMAGE_DIR
        return str(Path(base_dir) / filename)
    # Linux 절대 경로이지만 호스트 경로일 경우 컨테이너 마운트로 변환
    filename = resource_path.replace("\\", "/").split("/")[-1]
    base_dir = AD_VIDEO_DIR if ad_type == "video_clip" else AD_IMAGE_DIR
    if base_dir and not resource_path.startswith(base_dir):
        return str(Path(base_dir) / filename)
    return resource_path


def _extract_video_frame(video_path: str) -> str | None:
    """영상 33% 지점 프레임을 임시 JPEG로 추출. 실패 시 None."""
    import subprocess, tempfile
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30,
        )
        duration = float(result.stdout.strip())
        seek = duration * 0.33
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.close()
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(seek), "-i", video_path,
             "-vframes", "1", "-q:v", "2", tmp.name],
            capture_output=True, timeout=60,
        )
        if Path(tmp.name).stat().st_size > 0:
            return tmp.name
    except Exception as exc:
        logger.warning("Frame extraction failed for %s: %s", video_path, exc)
    return None


def _build_prompt(ad_category: str | None) -> str:
    if ad_category and ad_category.strip():
        return AD_NARRATIVE_PROMPT_WITH_CATEGORY.format(category=ad_category.strip())
    return AD_NARRATIVE_PROMPT


def _analyse_ad(prompt: str, image_path: str | None) -> str:
    """Gemini Flash로 광고 narrative 생성."""
    contents: list = []
    if image_path and Path(image_path).exists():
        data = Path(image_path).read_bytes()
        contents.append(types.Part.from_bytes(data=data, mime_type="image/jpeg"))
    contents.append(prompt)
    return _call_gemini(contents)


# ── DB 조회 ────────────────────────────────────────────────────────────────────

def _get_unprocessed_ads(limit: int | None, force: bool = False) -> list[dict]:
    if force:
        sql = """
            SELECT ad_id, ad_name, ad_type, resource_path, ad_category
            FROM ad_inventory
            ORDER BY ad_id
        """
    else:
        sql = """
            SELECT ad_id, ad_name, ad_type, resource_path, ad_category
            FROM ad_inventory
            WHERE target_narrative IS NULL OR target_narrative = ''
            ORDER BY ad_id
        """
    params: tuple = ()
    if limit:
        sql += f" LIMIT {int(limit)}"
    return _db.fetchall(sql, params)


def _save_narrative(ad_id: str, narrative: str | None) -> None:
    _db.execute(
        "UPDATE ad_inventory SET target_narrative=%s WHERE ad_id=%s",
        (narrative, ad_id),
    )


# ── 처리 ────────────────────────────────────────────────────────────────────────

def _process_ad(ad: dict) -> str | None:
    ad_type       = ad.get("ad_type", "")
    resource_path = ad.get("resource_path", "")
    ad_category   = ad.get("ad_category")
    prompt        = _build_prompt(ad_category)

    resolved = _resolve_path(resource_path, ad_type)
    tmp_frame: str | None = None

    if ad_type == "video_clip":
        if not resolved or not Path(resolved).exists():
            logger.warning("Video not found: %s", resolved)
            return None
        tmp_frame = _extract_video_frame(resolved)
        if not tmp_frame:
            logger.warning("[%s] Skipping — frame extraction failed.", ad["ad_name"])
            return None
        narrative = _analyse_ad(prompt, tmp_frame)
        Path(tmp_frame).unlink(missing_ok=True)

    elif ad_type == "banner":
        if not resolved or not Path(resolved).exists():
            logger.warning("Banner not found: %s", resolved)
            return None
        narrative = _analyse_ad(prompt, resolved)

    else:
        narrative = _analyse_ad(prompt, None)

    return narrative if narrative else None


def run(limit: int | None = None, dry_run: bool = False, force: bool = False) -> None:
    ads = _get_unprocessed_ads(limit, force=force)
    total = len(ads)
    logger.info("Found %d ads to process (force=%s, dry_run=%s)", total, force, dry_run)

    if dry_run:
        for ad in ads:
            print(f"[DRY-RUN] {ad['ad_id']} ({ad['ad_type']})")
        return

    success = null_stored = 0

    for i, ad in enumerate(ads, 1):
        logger.info("[%d/%d] Processing %s (%s) ...", i, total, ad["ad_name"], ad["ad_type"])
        narrative = _process_ad(ad)

        if narrative:
            _save_narrative(ad["ad_id"], narrative)
            logger.info(
                "[%s] narrative (%d chars): %s",
                ad["ad_name"], len(narrative), narrative[:80],
            )
            success += 1
        else:
            _save_narrative(ad["ad_id"], None)
            logger.warning("[%s] Stored NULL (will retry on next run).", ad["ad_name"])
            null_stored += 1

    logger.info("Done. success=%d, null_stored=%d / total=%d", success, null_stored, total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gemini Flash Ad Narrative Analyser")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    run(limit=args.limit, dry_run=args.dry_run, force=args.force)
