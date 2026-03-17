"""
vision_qwen.py — Scene Description via Qwen2-VL-2B-Instruct
─────────────────────────────────────────────────────────────
v2.0  : 기본 프레임 설명 (QWEN_SAMPLE_INTERVAL_SEC 간격)
v2.1  : analyse_silence_context() 추가 (키워드 태그)
v2.2  : analyse_context_narrative() 추가 (서술문, semantic 매칭용)
v2.5  : analyse_scene_context() 추가 (정방향 씬 분석, 멀티프레임 입력)
        _clean_vlm_response() 추가 (마크다운·개행 정규화)
        프롬프트 간결화: 분량 강제 제거, 팩트 중심

VLM Graceful-Degradation 정책:
  - 추론 실패 시 "" / [] 반환, 파이프라인 중단 없음
  - _clean_vlm_response()로 모델이 지시 무시해도 단일 서술문 강제
"""

import logging
import math
import re
from pathlib import Path

import torch
from transformers import AutoProcessor, Qwen2VLForConditionalGeneration

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common.config import QWEN_MAX_SAMPLES, QWEN_SAMPLE_INTERVAL_SEC

logger = logging.getLogger(__name__)

# ── Model config ──────────────────────────────────────────────────────────────
MODEL_ID       = "Qwen/Qwen2-VL-2B-Instruct"
MAX_NEW_TOKENS = 128

PROMPT = (
    "이 영상 프레임의 장면, 분위기, 상황을 한국어로 간결하게 설명하세요. "
    "주요 인물, 배경 설정, 전반적인 감정 톤을 포함하세요. "
    "3문장 이하로 작성하세요."
)

# ── singleton ─────────────────────────────────────────────────────────────────
_model = None
_processor = None
_device = None


def _get_model():
    global _model, _processor, _device
    if _model is None:
        _device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("Loading Qwen2-VL on %s ...", _device)
        _model = Qwen2VLForConditionalGeneration.from_pretrained(
            MODEL_ID,
            torch_dtype=torch.float16 if _device == "cuda" else torch.float32,
            device_map="auto" if _device == "cuda" else None,
        )
        if _device == "cpu":
            _model = _model.to("cpu")
        _processor = AutoProcessor.from_pretrained(MODEL_ID)
        logger.info("Qwen2-VL loaded.")
    return _model, _processor


# ── Text post-processing ──────────────────────────────────────────────────────

def _clean_vlm_response(text: str) -> str:
    """
    VLM 출력 정규화.

    소형 VLM(2B)이 "1 sentence" 지시를 무시하거나 목록·개행으로 답변할 때
    단일 연속 문자열로 변환하고 마크다운 잔재를 제거한다.

    처리 순서:
    1. 마크다운 굵기/이탤릭 (* / **)
    2. 마크다운 헤더 (# / ## / …)
    3. 글머리 기호 (-, •, *, 숫자 목록)
    4. 개행 → 공백
    5. 중복 공백 제거
    """
    if not text:
        return ""

    # 마크다운 볼드/이탤릭 제거 (기호만 제거, 내용은 보존)
    text = re.sub(r'\*{1,3}([^*\n]+)\*{1,3}', r'\1', text)
    # 마크다운 헤더
    text = re.sub(r'^\s*#{1,6}\s+', '', text, flags=re.MULTILINE)
    # 글머리 기호 (줄 시작)
    text = re.sub(r'^\s*[-•]\s+', '', text, flags=re.MULTILINE)
    # 숫자 목록 (1. / 2. 등)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    # 개행 → 공백
    text = re.sub(r'\n+', ' ', text)
    # 중복 공백
    text = re.sub(r'\s{2,}', ' ', text)

    return text.strip()


# ── Frame description (single frame) ─────────────────────────────────────────

def _describe_frame(frame_path: str) -> str:
    """Return a scene description for a single frame."""
    model, processor = _get_model()

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": frame_path},
                {"type": "text",  "text": PROMPT},
            ],
        }
    ]

    text = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    inputs = processor(
        text=[text],
        images=[frame_path],
        padding=True,
        return_tensors="pt",
    )
    inputs = {k: v.to(_device) for k, v in inputs.items()}

    with torch.no_grad():
        generated = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS)

    trimmed = generated[0][inputs["input_ids"].shape[-1]:]
    return processor.decode(trimmed, skip_special_tokens=True).strip()


def _compute_sample_interval(total_frames: int) -> int:
    """Adaptive sampling interval to cap total Qwen calls at QWEN_MAX_SAMPLES."""
    if total_frames == 0:
        return QWEN_SAMPLE_INTERVAL_SEC

    base_samples = math.ceil(total_frames / QWEN_SAMPLE_INTERVAL_SEC)
    if base_samples <= QWEN_MAX_SAMPLES:
        return QWEN_SAMPLE_INTERVAL_SEC

    return math.ceil(total_frames / QWEN_MAX_SAMPLES)


# ── v2.5: Scene context (multimodal, forward-direction) ──────────────────────

def analyse_scene_context(
    frame_paths: list[str],
    transcript_text: str,
    scene_start_sec: float,
    scene_end_sec: float,
) -> str:
    """
    씬 전체의 멀티모달 컨텍스트를 분석하여 간결한 서술문을 반환한다. (v2.5 신규)

    침묵 역추적 방식 대신 씬 분절 후 정방향 분석.
    frame_paths는 consumer.py에서 씬 구간 내 균등 샘플링한 3~5장.

    Args:
        frame_paths:      씬 내 균등 샘플링된 프레임 경로 목록 (0개 가능).
        transcript_text:  씬 구간의 대사 텍스트 (한국어, Whisper 원문).
        scene_start_sec:  씬 시작 시각 (로그용).
        scene_end_sec:    씬 종료 시각 (로그용).

    Returns:
        광고 매칭용 서술문 문자열. 실패 시 "".
    """
    model, processor = _get_model()

    transcript_excerpt = (transcript_text or "").strip()[:1200]

    # 프레임 존재 여부에 따라 멀티모달 / 텍스트 전용 전환
    valid_frames = [fp for fp in (frame_paths or []) if Path(fp).exists()]

    # ── 프롬프트 구성 ────────────────────────────────────────────────────────
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
        "반드시 한국어로만 답하세요. Do not use English. 영어 사용 금지."
    )

    # ── 멀티프레임 입력 빌드 ─────────────────────────────────────────────────
    if valid_frames:
        content: list[dict] = [
            {"type": "image", "image": fp} for fp in valid_frames
        ]
        content.append({"type": "text", "text": prompt})
        messages = [{"role": "user", "content": content}]

        text = processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        try:
            inputs = processor(
                text=[text],
                images=valid_frames,
                padding=True,
                return_tensors="pt",
            )
        except Exception as exc:
            # 일부 프레임 손상 시 텍스트 전용으로 폴백
            logger.warning(
                "analyse_scene_context: multi-image input failed (%.1f-%.1fs): %s — falling back to text-only",
                scene_start_sec, scene_end_sec, exc,
            )
            valid_frames = []

    if not valid_frames:
        # 텍스트 전용 모드
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
        text = processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = processor(text=[text], padding=True, return_tensors="pt")

    inputs = {k: v.to(_device) for k, v in inputs.items()}

    try:
        with torch.no_grad():
            generated = model.generate(**inputs, max_new_tokens=160)
    except Exception as exc:
        logger.warning(
            "analyse_scene_context: inference failed (%.1f-%.1fs): %s",
            scene_start_sec, scene_end_sec, exc,
        )
        return ""

    trimmed = generated[0][inputs["input_ids"].shape[-1]:]
    raw = processor.decode(trimmed, skip_special_tokens=True)
    result = _clean_vlm_response(raw)

    logger.info(
        "analyse_scene_context [%.1f–%.1fs] %d frame(s) → %d chars: %s",
        scene_start_sec, scene_end_sec, len(valid_frames), len(result), result[:80],
    )
    return result


# ── v2.1: Keyword tags (per-silence, kept for backward compat) ───────────────

def analyse_silence_context(
    transcript_before: str,
    scene_descriptions: list[str],
    silence_start_sec: float,
) -> list[str]:
    """
    침묵 구간 직전 컨텍스트 → 광고 매칭 키워드 목록.

    v2.5에서는 analyse_scene_context()가 주 경로이며, 이 함수는
    analysis_audio.context_tags 백필 및 레거시 호환용으로 유지.

    Returns:
        List of lowercase English keywords (max 12).
    """
    model, processor = _get_model()

    transcript_excerpt = (transcript_before or "").strip()[:1000]
    desc_excerpt = " | ".join(d for d in scene_descriptions[-5:] if d)[:500]

    if not transcript_excerpt and not desc_excerpt:
        logger.warning(
            "analyse_silence_context: no content at %.1fs — returning []",
            silence_start_sec,
        )
        return []

    ctx_parts: list[str] = []
    if transcript_excerpt:
        ctx_parts.append(f"대사:\n{transcript_excerpt}")
    if desc_excerpt:
        ctx_parts.append(f"시각 장면:\n{desc_excerpt}")
    context_text = "\n\n".join(ctx_parts)

    prompt = (
        "다음 TV 장면에서 광고 삽입에 적합한 맥락을 설명하는 핵심 키워드를 최대 12개 추출하세요. "
        "감정 톤, 배경, 인물의 필요와 욕구, 주제에 집중하세요.\n\n"
        f"{context_text}\n\n"
        "쉼표로 구분된 키워드 목록만 반환하세요. 설명은 생략하세요."
    )

    messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=[text], padding=True, return_tensors="pt")
    inputs = {k: v.to(_device) for k, v in inputs.items()}

    try:
        with torch.no_grad():
            generated = model.generate(**inputs, max_new_tokens=150)
    except Exception as exc:
        logger.warning("analyse_silence_context: inference failed at %.1fs: %s", silence_start_sec, exc)
        return []

    trimmed = generated[0][inputs["input_ids"].shape[-1]:]
    raw = processor.decode(trimmed, skip_special_tokens=True).strip()

    tags = [t.strip().lower() for t in raw.replace("\n", ",").split(",") if t.strip()]
    tags = [t for t in tags if 1 <= len(t) <= 40 and len(t.split()) <= 3]
    tags = list(dict.fromkeys(tags))[:12]

    logger.info(
        "analyse_silence_context @ %.1fs → %d tags: %s",
        silence_start_sec, len(tags), tags,
    )
    return tags


# ── v2.2: Context narrative (per-silence, kept for backward compat) ───────────

def analyse_context_narrative(
    transcript_before: str,
    scene_descriptions: list[str],
    silence_start_sec: float,
) -> str:
    """
    침묵 구간 직전 컨텍스트 → 광고 매칭용 서술문.

    v2.5에서는 씬 단위 analyse_scene_context()가 주 경로.
    이 함수는 레거시 backfill 스크립트 호환용으로 유지하되
    프롬프트를 간결화 (분량 강제 제거).

    Returns:
        1-2 sentence English narrative, or "".
    """
    model, processor = _get_model()

    transcript_excerpt = (transcript_before or "").strip()[:1000]
    desc_excerpt = " | ".join(d for d in scene_descriptions[-5:] if d)[:500]

    if not transcript_excerpt and not desc_excerpt:
        logger.warning(
            "analyse_context_narrative: no content at %.1fs — returning ''",
            silence_start_sec,
        )
        return ""

    ctx_parts: list[str] = []
    if transcript_excerpt:
        ctx_parts.append(f"대사:\n{transcript_excerpt}")
    if desc_excerpt:
        ctx_parts.append(f"시각 장면:\n{desc_excerpt}")
    context_text = "\n\n".join(ctx_parts)

    prompt = (
        "광고 삽입 맥락 파악을 위해 다음 TV 장면을 한국어로 설명하세요.\n\n"
        f"{context_text}\n\n"
        "1~2문장으로 등장인물, 행동, 감정 분위기를 사실적으로 서술하세요. "
        "광고 카테고리는 언급하지 마세요."
    )

    messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=[text], padding=True, return_tensors="pt")
    inputs = {k: v.to(_device) for k, v in inputs.items()}

    try:
        with torch.no_grad():
            generated = model.generate(**inputs, max_new_tokens=160)
    except Exception as exc:
        logger.warning("analyse_context_narrative: inference failed at %.1fs: %s", silence_start_sec, exc)
        return ""

    trimmed = generated[0][inputs["input_ids"].shape[-1]:]
    raw = processor.decode(trimmed, skip_special_tokens=True)
    narrative = _clean_vlm_response(raw)

    logger.info(
        "analyse_context_narrative @ %.1fs → %d chars: %s",
        silence_start_sec, len(narrative), narrative[:80],
    )
    return narrative


# ── v2.0: Bulk frame analysis (Qwen sampling pass) ───────────────────────────

def analyse_frames(frame_paths: list[str]) -> dict[int, str]:
    """
    Adaptive sampling + scene description for all frames.

    Returns:
        {frame_index: scene_description_str}
    """
    sorted_paths = sorted(frame_paths)
    total = len(sorted_paths)

    if total == 0:
        return {}

    interval = _compute_sample_interval(total)
    sampled_indices = list(range(0, total, interval))

    logger.info(
        "Qwen2-VL sampling: %d frames total, interval=%ds, samples=%d",
        total, interval, len(sampled_indices),
    )

    descriptions: dict[int, str] = {}
    total_samples = len(sampled_indices)

    for i, idx in enumerate(sampled_indices):
        fpath = sorted_paths[idx]
        try:
            desc = _describe_frame(fpath)
            descriptions[idx] = desc
            logger.info(
                "Qwen2-VL [%d/%d] frame %d (%.1f s): %s",
                i + 1, total_samples, idx, float(idx), desc[:60],
            )
        except Exception as exc:
            logger.warning("Qwen2-VL failed on frame %d: %s", idx, exc)
            descriptions[idx] = ""

    return descriptions
