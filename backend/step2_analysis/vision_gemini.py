"""
vision_gemini.py — Scene Description & Context via Gemini Flash (v2.0)
──────────────────────────────────────────────────────────────────────
Qwen2-VL(vision_qwen.py)과 동일한 함수 시그니처를 유지하면서
Google Gemini Flash API로 대체하는 모듈.

config.VLM_BACKEND = "gemini" 일 때 consumer.py에서 이 모듈을 사용.

Rate limit (무료 티어):
  - 15 RPM (분당 15회) → 호출 간 최소 4초 대기
  - 1,500 RPD (일 1500회)

환경변수:
  GEMINI_API_KEY: Google AI Studio에서 발급한 API 키
  GEMINI_MODEL:   사용할 모델 (기본: gemini-2.0-flash)

변경사항 (v2.0):
  - google.generativeai (deprecated) → google.genai (신규 SDK) 교체
"""

import base64
import logging
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from google import genai
from google.genai import types
from common import config

logger = logging.getLogger(__name__)

# ── 모델 설정 ──────────────────────────────────────────────────────────────────
_MODEL_NAME   = getattr(config, "GEMINI_MODEL", "gemini-2.0-flash")
_API_KEY      = getattr(config, "GEMINI_API_KEY", "")
_RPM_INTERVAL = 4.0   # 15 RPM → 최소 4초 간격

_last_call_time: float = 0.0
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not _API_KEY:
            raise RuntimeError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")
        _client = genai.Client(api_key=_API_KEY)
        logger.info("Gemini Flash client initialised. model=%s", _MODEL_NAME)
    return _client


def _rate_limit() -> None:
    """RPM 제한 준수를 위한 대기."""
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < _RPM_INTERVAL:
        time.sleep(_RPM_INTERVAL - elapsed)
    _last_call_time = time.time()


def _image_part(path: str) -> types.Part:
    """이미지 파일을 Gemini Part로 변환."""
    data = Path(path).read_bytes()
    return types.Part.from_bytes(data=data, mime_type="image/jpeg")


def _call_gemini(contents: list, max_retries: int = 3) -> str:
    """Gemini API 호출 + 재시도 (429 rate limit 대응)."""
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
                logger.warning(
                    "Gemini rate limit hit — waiting %ds (attempt %d/%d)",
                    wait, attempt + 1, max_retries,
                )
                time.sleep(wait)
            else:
                logger.warning(
                    "Gemini API error (attempt %d/%d): %s",
                    attempt + 1, max_retries, exc,
                )
                if attempt == max_retries - 1:
                    return ""
    return ""


# ── vision_qwen.py 호환 함수들 ─────────────────────────────────────────────────

def analyse_frames(frame_paths: list[str]) -> dict[int, str]:
    """
    vision_qwen.analyse_frames()와 동일한 시그니처.
    샘플링된 프레임에 대해 Gemini Flash로 장면 설명을 생성한다.

    Returns:
        {frame_index: scene_description_str}
    """
    sorted_paths = sorted(frame_paths)
    total = len(sorted_paths)
    if total == 0:
        return {}

    # 적응형 샘플링 (최대 60회 API 호출)
    QWEN_MAX_SAMPLES = 60
    interval = max(1, total // QWEN_MAX_SAMPLES)
    sampled_indices = list(range(0, total, interval))

    logger.info(
        "Gemini sampling: %d frames total, interval=%d, samples=%d",
        total, interval, len(sampled_indices),
    )

    prompt = (
        "이 TV 드라마 프레임의 장면, 분위기, 상황을 한국어로 간결하게 설명하세요. "
        "1~2문장으로 등장인물, 배경, 분위기를 포함하세요. "
        "반드시 한국어로만 답하세요."
    )

    descriptions: dict[int, str] = {}
    total_samples = len(sampled_indices)

    for i, idx in enumerate(sampled_indices):
        fpath = sorted_paths[idx]
        if not Path(fpath).exists():
            continue

        contents = [_image_part(fpath), prompt]
        result = _call_gemini(contents)
        descriptions[idx] = result

        logger.info(
            "Gemini [%d/%d] frame %d: %s",
            i + 1, total_samples, idx, result[:80],
        )

    return descriptions


def analyse_scene_context(
    frame_paths: list[str],
    transcript_text: str,
    scene_start_sec: float,
    scene_end_sec: float,
) -> str:
    """
    vision_qwen.analyse_scene_context()와 동일한 시그니처.
    씬 전체의 멀티모달 컨텍스트를 Gemini Flash로 분석하여 한국어 서술문을 반환한다.

    Returns:
        광고 매칭용 한국어 서술문. 실패 시 "".
    """
    transcript_excerpt = (transcript_text or "").strip()[:1200]
    valid_frames = [fp for fp in (frame_paths or []) if Path(fp).exists()]

    if not valid_frames and not transcript_excerpt:
        logger.warning(
            "analyse_scene_context: no content (%.1f-%.1fs) — returning ''",
            scene_start_sec, scene_end_sec,
        )
        return ""

    # 프롬프트 구성
    ctx_parts: list[str] = []
    if transcript_excerpt:
        ctx_parts.append(f"대사: {transcript_excerpt}")
    context_block = "\n".join(ctx_parts) if ctx_parts else "(대사 없음)"

    prompt = (
        "당신은 한국 TV 드라마 장면을 분석하여 광고 매칭에 활용할 컨텍스트를 생성하는 전문가입니다.\n\n"
        f"{context_block}\n\n"
        "1~2문장으로 다음을 한국어로 설명하세요:\n"
        "- 등장인물과 그들이 하는 행동\n"
        "- 감정적 분위기\n"
        "- 인물들의 욕구나 필요\n\n"
        "사실에 근거하여 구체적으로 작성하세요. 광고 카테고리나 브랜드명은 언급하지 마세요.\n\n"
        "반드시 한국어로만 답하세요. Do not use English."
    )

    # 이미지 파트 빌드 (최대 5장)
    contents: list = []
    for fp in valid_frames[:5]:
        contents.append(_image_part(fp))
    contents.append(prompt)

    result = _call_gemini(contents)

    logger.info(
        "analyse_scene_context [%.1f–%.1fs] %d frame(s) → %d chars: %s",
        scene_start_sec, scene_end_sec, len(valid_frames), len(result), result[:80],
    )
    return result
