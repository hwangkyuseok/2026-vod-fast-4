"""
vision_gemini.py — Scene Description & Context via Gemini Flash (v2.1)
──────────────────────────────────────────────────────────────────────
Qwen2-VL(vision_qwen.py)과 동일한 함수 시그니처를 유지하면서
Google Gemini Flash API로 대체하는 모듈.

config.VLM_BACKEND = "gemini" 일 때 consumer.py에서 이 모듈을 사용.

Rate limit (무료 티어):
  - 15 RPM (분당 15회) → 호출 간 최소 4초 대기
  - 1,500 RPD (일 1500회)

환경변수:
  GEMINI_API_KEY: Google AI Studio에서 발급한 API 키
  GEMINI_MODEL:   사용할 모델 (기본: gemini-2.5-flash-preview-04-17)

변경사항 (v2.1):
  - 프롬프트: 상황/감정/욕구 3항목 형식으로 변경
  - analyse_scene_context(): detected_objects 파라미터 추가
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


_PROMPT_TEMPLATE = (
    "아래는 한국 TV 드라마의 한 씬에 대한 정보입니다.\n\n"
    "씬 구간: {scene_start_sec:.1f}s ~ {scene_end_sec:.1f}s\n\n"
    "[화면 속 탐지된 객체]\n"
    "{detected_objects}\n\n"
    "[대사]\n"
    "{dialogue_text}\n\n"
    "위 객체와 대사 정보를 반드시 참고하여, 이 씬을 분석하고 아래 세 항목을 각각 정확히 1문장으로 작성하세요.\n"
    "반드시 'label: 내용' 형식을 지켜야 합니다.\n\n"
    "상황: 이 씬에 등장하는 장면, 배경, 인물의 행동을 묘사하세요. (감정·느낌 표현 금지)\n"
    "감정: 이 씬이 시청자에게 전달하는 감성적 분위기나 정서를 표현하세요. (상황 묘사 금지)\n"
    "욕구: 이 씬을 본 시청자가 느끼는 소비 욕구를 서술하세요. "
    "(예: 금융상품 가입 충동, 음식 섭취 욕구, 여행 충동, 뷰티·패션 구매 욕구 등 구체적 소비 행동으로 표현. 감정 단어·상황 묘사 금지)\n\n"
    "예시:\n"
    "상황: 퇴근 후 집에 돌아온 직장인이 소파에 앉아 따뜻한 음료를 마시는 장면이다.\n"
    "감정: 하루의 피로가 녹아드는 포근하고 안도감 있는 분위기를 전달한다.\n"
    "욕구: 따뜻한 음료나 간식을 즉시 구매하거나, 편안한 홈웨어·안마기 등 휴식 관련 상품을 구매하고 싶어진다.\n\n"
    "반드시 한국어로만 작성하고, 위 예시처럼 세 줄로만 답하세요."
)


def analyse_scene_context(
    frame_paths: list[str],
    transcript_text: str,
    scene_start_sec: float,
    scene_end_sec: float,
    detected_objects: str = "",
) -> str:
    """
    vision_qwen.analyse_scene_context()와 동일한 시그니처 (detected_objects 추가).
    씬 프레임(최대 3장) + YOLO 객체 + Whisper 대사를 Gemini에 전달하여
    상황/감정/욕구 3항목 형식의 context_narrative를 반환한다.

    Returns:
        "상황: ... 감정: ... 욕구: ..." 형식 한국어 서술문. 실패 시 "".
    """
    transcript_excerpt = (transcript_text or "").strip()[:1200]
    valid_frames = [fp for fp in (frame_paths or []) if Path(fp).exists()]

    if not valid_frames and not transcript_excerpt:
        logger.warning(
            "analyse_scene_context: no content (%.1f-%.1fs) — returning ''",
            scene_start_sec, scene_end_sec,
        )
        return ""

    objects_text  = detected_objects.strip() if detected_objects.strip() else "(탐지 없음)"
    dialogue_text = transcript_excerpt if transcript_excerpt else "(대사 없음)"

    prompt = _PROMPT_TEMPLATE.format(
        scene_start_sec=scene_start_sec,
        scene_end_sec=scene_end_sec,
        detected_objects=objects_text,
        dialogue_text=dialogue_text,
    )

    # 프레임 이미지 (최대 3장) + 프롬프트
    contents: list = []
    for fp in valid_frames[:3]:
        contents.append(_image_part(fp))
    contents.append(prompt)

    result = _call_gemini(contents)

    logger.info(
        "analyse_scene_context [%.1f–%.1fs] %d frame(s) → %d chars: %s",
        scene_start_sec, scene_end_sec, len(valid_frames), len(result), result[:80],
    )
    return result
