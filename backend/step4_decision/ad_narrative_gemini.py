"""
analyze_ad_narrative_gemini.py — Ad Narrative 생성 (Gemini Flash 버전)
──────────────────────────────────────────────────────────────────────
analyze_ad_narrative.py (Qwen2-VL)과 동일한 로직을 Gemini Flash API로 대체.

실행:
    python analyze_ad_narrative_gemini.py [--limit N] [--dry-run] [--force]

환경변수:
    GEMINI_API_KEY  : Google AI Studio API 키
    GEMINI_MODEL    : 모델명 (기본: gemini-2.0-flash)

변경사항 (v2.0):
    - google.generativeai (deprecated) → google.genai (신규 SDK) 교체
"""

"""

변경사항 (v2.1):
    - google.generativeai (deprecated) → google.genai (신규 SDK) 교체
    - --start-id 옵션 추가 (force 사용 시 특정 ID부터 재개 가능)
    - 429 Rate Limit 대기 시간 및 RPM 인터벌 단축 (유료 결제 기준)
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

setup_logging("analyze_ad_narrative_gemini")
logger = logging.getLogger(__name__)

# ── 설정 ────────────────────────────────────────────────────────────────────────
# 404 에러 방지를 위해 2.5-flash로 기본값 변경
# 여러 모델을 번갈아 쓰기 위해 리스트로 선언 (성능이 좋고 빠른 Flash 계열 위주)
_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest"
]
_model_index = 0  # 번갈아 쓰기 위한 인덱스
GEMINI_API_KEY = getattr(config, "GEMINI_API_KEY", "")
_RPM_INTERVAL = 1.0   # 여러 모델을 분산 호출하므로 간격을 1초로 줄임
AD_VIDEO_DIR = getattr(config, "AD_VIDEO_DIR", "")
AD_IMAGE_DIR = getattr(config, "AD_IMAGE_DIR", "")

# ── Narrative 프롬프트 ─────────────────────────────────────────────────────────
AD_NARRATIVE_PROMPT = (
    "이 광고 영상/이미지를 분석하여 아래 세 항목을 각각 정확히 1문장으로 작성하세요.\n"
    "반드시 'label: 내용' 형식을 지켜야 합니다.\n\n"
    "상황: 이 광고에 등장하는 장면, 배경, 인물의 행동을 묘사하세요. (감정·느낌 표현 금지)\n"
    "감정: 이 광고가 시청자에게 전달하는 감성적 분위기나 정서를 표현하세요. (상황 묘사 금지)\n"
    "욕구: 이 광고가 타겟하는 시청자의 내면적 니즈나 욕구를 서술하세요. (감정 단어·상황 묘사 금지)\n\n"
    "예시:\n"
    "상황: 퇴근 후 집에 돌아온 직장인이 소파에 앉아 따뜻한 음료를 마시는 장면이다.\n"
    "감정: 하루의 피로가 녹아드는 포근하고 안도감 있는 분위기를 전달한다.\n"
    "욕구: 바쁜 일상 속에서 잠깐의 휴식과 자신을 위한 작은 여유를 원하는 사람에게 어울린다.\n\n"
    "반드시 한국어로만 작성하고, 위 예시처럼 세 줄로만 답하세요."
)

AD_NARRATIVE_PROMPT_WITH_CATEGORY = (
    "이 광고는 '{category}' 카테고리의 광고입니다.\n\n"
    "이 광고 영상/이미지를 분석하여 아래 세 항목을 각각 정확히 1문장으로 작성하세요.\n"
    "반드시 'label: 내용' 형식을 지켜야 합니다.\n\n"
    "상황: 이 광고에 등장하는 장면, 배경, 인물의 행동을 묘사하세요. (감정·느낌 표현 금지)\n"
    "감정: 이 광고가 시청자에게 전달하는 감성적 분위기나 정서를 표현하세요. (상황 묘사 금지)\n"
    "욕구: 이 광고가 타겟하는 시청자의 내면적 니즈나 욕구를 서술하세요. (감정 단어·상황 묘사 금지)\n\n"
    "예시:\n"
    "상황: 퇴근 후 집에 돌아온 직장인이 소파에 앉아 따뜻한 음료를 마시는 장면이다.\n"
    "감정: 하루의 피로가 녹아드는 포근하고 안도감 있는 분위기를 전달한다.\n"
    "욕구: 바쁜 일상 속에서 잠깐의 휴식과 자신을 위한 작은 여유를 원하는 사람에게 어울린다.\n\n"
    "반드시 한국어로만 작성하고, 위 예시처럼 세 줄로만 답하세요."
)

# ── Gemini 클라이언트 ──────────────────────────────────────────────────────────
_client: genai.Client | None = None
_last_call_time: float = 0.0


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")
        _client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("Gemini Flash client initialised.")
    return _client


def _rate_limit() -> None:
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < _RPM_INTERVAL:
        time.sleep(_RPM_INTERVAL - elapsed)
    _last_call_time = time.time()

def _call_gemini(contents: list, max_retries: int = 5) -> str:
    global _model_index
    client = _get_client()
    
    for attempt in range(max_retries):
        _rate_limit()
        
        # 이번 턴에 사용할 모델 선택 (0번, 1번, 2번... 순서대로 뺑뺑이)
        current_model = _MODELS[_model_index % len(_MODELS)]
        _model_index += 1
        
        try:
            response = client.models.generate_content(
                model=current_model,
                contents=contents,
            )
            return (response.text or "").strip()
        
        except Exception as exc:
            if "429" in str(exc) or "quota" in str(exc).lower():
                logger.warning(f"Rate limit on {current_model}. Switching model... (attempt {attempt+1}/{max_retries})")
                # 오래 대기하지 않고 1.5초만 숨 고르고 바로 다음 모델로 루프 돕니다.
                time.sleep(1.5)
            else:
                logger.warning(f"Gemini error on {current_model} (attempt {attempt+1}/{max_retries}): {exc}")
                if attempt == max_retries - 1:
                    return ""
    return ""

# ── 유틸리티 ───────────────────────────────────────────────────────────────────

def _resolve_path(resource_path: str, ad_type: str) -> str:
    if not resource_path:
        return ""
    if len(resource_path) >= 3 and resource_path[1] == ":":
        filename = resource_path.replace("\\", "/").split("/")[-1]
        base_dir = AD_VIDEO_DIR if ad_type == "video_clip" else AD_IMAGE_DIR
        return str(Path(base_dir) / filename)
    filename = resource_path.replace("\\", "/").split("/")[-1]
    base_dir = AD_VIDEO_DIR if ad_type == "video_clip" else AD_IMAGE_DIR
    if base_dir and not resource_path.startswith(base_dir):
        return str(Path(base_dir) / filename)
    return resource_path


def _extract_video_frame(video_path: str) -> str | None:
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
    contents: list = []
    if image_path and Path(image_path).exists():
        data = Path(image_path).read_bytes()
        contents.append(types.Part.from_bytes(data=data, mime_type="image/jpeg"))
    contents.append(prompt)
    return _call_gemini(contents)


# ── DB 조회 ────────────────────────────────────────────────────────────────────

def _get_unprocessed_ads(limit: int | None, force: bool = False, start_id: int | None = None) -> list[dict]:
    params: list = []
    
    if force:
        if start_id:
            # start_id가 주어지면 해당 ID부터 덮어쓰기 시작
            sql = """
                SELECT ad_id, ad_name, ad_type, resource_path, ad_category
                FROM ad_inventory
                WHERE ad_id >= %s
                ORDER BY ad_id
            """
            params.append(start_id)
        else:
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
        
    if limit:
        sql += f" LIMIT {int(limit)}"
        
    return _db.fetchall(sql, tuple(params))


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


'''
ID 순서 정렬 & 필터링하기
'''
def run(limit: int | None = None, dry_run: bool = False, force: bool = False, start_id: str | None = None) -> None:
    # 1. DB에서 일단 리스트를 다 가져옵니다.
    ads = _get_unprocessed_ads(limit, force=force)

    # 2. start_id가 입력되었다면 숫자(list index)이거나 문자열 ID일 수 있습니다.
    offset = 0
    if start_id:
        if start_id.isdigit():
            # 1-based index 입력으로 간주 (예: 123)
            idx = int(start_id) - 1
            if 0 <= idx < len(ads):
                offset = idx
        else:
            # 문자열 형태의 ad_id를 직접 입력했을 경우
            for i, ad in enumerate(ads):
                if ad['ad_id'] == start_id or ad['ad_id'] >= start_id:
                    offset = i
                    break
        ads = ads[offset:]

    total = len(ads) + offset
    logger.info("Found %d ads to process (force=%s, dry_run=%s, start_id=%s)", total, force, dry_run, start_id)

    if dry_run:
        for ad in ads:
            print(f"[DRY-RUN] {ad['ad_id']} ({ad['ad_type']})")
        return

    success = null_stored = 0

    for i, ad in enumerate(ads, offset + 1):
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
    # 시작할 ID를 직접 입력받는 파라미터 추가
    parser.add_argument("--start-id", type=str, default=None, help="이어서 덮어쓰기 시작할 ad_id")
    args = parser.parse_args()
    
    run(limit=args.limit, dry_run=args.dry_run, force=args.force, start_id=args.start_id)