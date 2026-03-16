"""
analyze_ad_narrative.py — 광고 target_narrative 일괄 생성 (v2.5)
────────────────────────────────────────────────────────────────────
ad_inventory 테이블의 모든 광고에 대해 Qwen2-VL 4차원 분석을 수행하고
target_narrative TEXT 컬럼을 채운다.

4차원 분석:
  1. Category     — 산업군/제품군
  2. Audience     — 타겟 고객 (연령·성별·관심사·직업군)
  3. Core Message — 해결하는 니즈 또는 제공하는 핵심 가치
  4. Ad Vibe      — 광고 전반의 분위기

멱등성 (Resume 기능):
  실행 중 중단 시 이미 target_narrative가 채워진 row는 자동 Skip.
  미처리 광고만 이어서 분석함.

VLM 예외 처리:
  - 추론 실패/타임아웃 → 빈 문자열 저장, 에러 로그, 파이프라인 계속
  - 응답 텍스트 후처리 (_clean_vlm_response): 개행·마크다운 정규화

실행:
    # 전체 미처리 광고 분석
    python analyze_ad_narrative.py

    # 최대 N개만 (테스트용)
    python analyze_ad_narrative.py --limit 5

    # dry-run (분석하지 않고 미처리 수만 출력)
    python analyze_ad_narrative.py --dry-run
"""

import argparse
import logging
import re
import subprocess
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))

import torch
from transformers import AutoProcessor, Qwen2VLForConditionalGeneration

from common import db as _db
from common.config import AD_IMAGE_DIR, AD_VIDEO_DIR
from common.logging_setup import setup_logging

setup_logging("analyze_ad_narrative")
logger = logging.getLogger(__name__)

# ── Qwen2-VL 설정 ─────────────────────────────────────────────────────────────
MODEL_ID = "Qwen/Qwen2-VL-2B-Instruct"
MAX_NEW_TOKENS = 200
FALLBACK_NARRATIVE = ""   # 분석 실패 시 저장되는 기본값 (빈 문자열 = 미분석 표시)

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


# ── 텍스트 후처리 ─────────────────────────────────────────────────────────────

def _clean_vlm_response(text: str) -> str:
    """
    VLM 출력 정규화.
    소형 모델(2B)이 지시를 무시하고 목록·개행으로 답변할 때 단일 문자열로 변환.
    """
    if not text:
        return ""
    text = re.sub(r'\*{1,3}([^*\n]+)\*{1,3}', r'\1', text)   # 볼드/이탤릭 제거
    text = re.sub(r'^\s*#{1,6}\s+', '', text, flags=re.MULTILINE)  # 헤더 제거
    text = re.sub(r'^\s*[-•]\s+', '', text, flags=re.MULTILINE)    # 글머리 제거
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)   # 번호 목록 제거
    text = re.sub(r'\n+', ' ', text)     # 개행 → 공백
    text = re.sub(r'\s{2,}', ' ', text)  # 중복 공백 제거
    return text.strip()


# ── 광고 분석 프롬프트 ────────────────────────────────────────────────────────

AD_NARRATIVE_PROMPT = (
    "이 광고 이미지 또는 영상 프레임을 분석하세요.\n\n"
    "다음 네 가지 차원을 모두 포함하는 하나의 자연스러운 한국어 문장을 작성하세요:\n"
    "1. 카테고리: 광고하는 제품 또는 서비스\n"
    "2. 타겟 고객: 연령대, 성별, 라이프스타일 등\n"
    "3. 핵심 메시지: 광고가 해결하는 니즈 또는 제공하는 핵심 가치\n"
    "4. 광고 분위기: 전반적인 감성 톤과 느낌\n\n"
    "사물을 나열하거나 시각적 묘사만 하지 마세요. "
    "해석적이고 자연스러운 문장으로 작성하세요.\n"
    "예시: '30~40대 여성을 대상으로 한 프리미엄 스킨케어 브랜드로, "
    "젊은 피부와 자기 관리의 여유로움을 약속하며 우아하고 세련된 분위기를 전달합니다.'"
)

AD_NARRATIVE_PROMPT_WITH_CATEGORY = (
    "이 광고 이미지 또는 영상 프레임을 분석하세요.\n\n"
    "이 광고의 카테고리는 [{category}] 입니다.\n\n"
    "다음 네 가지 차원을 모두 포함하는 하나의 자연스러운 한국어 문장을 작성하세요:\n"
    "1. 카테고리: 광고하는 제품 또는 서비스\n"
    "2. 타겟 고객: 연령대, 성별, 라이프스타일 등\n"
    "3. 핵심 메시지: 광고가 해결하는 니즈 또는 제공하는 핵심 가치\n"
    "4. 광고 분위기: 전반적인 감성 톤과 느낌\n\n"
    "사물을 나열하거나 시각적 묘사만 하지 마세요. "
    "해석적이고 자연스러운 문장으로 작성하세요.\n"
    "예시: '30~40대 여성을 대상으로 한 프리미엄 스킨케어 브랜드로, "
    "젊은 피부와 자기 관리의 여유로움을 약속하며 우아하고 세련된 분위기를 전달합니다.'"
)


def _build_prompt(ad_category: str | None) -> str:
    """
    ad_category 존재 여부에 따라 프롬프트를 선택한다.
    카테고리가 있으면 해당 정보를 프롬프트에 주입하여 VLM이 더 정확한 분석을 하도록 유도.
    카테고리가 없으면 (기존 238개 등) 기본 프롬프트 사용 — 동작 변경 없음.
    """
    if ad_category and ad_category.strip():
        return AD_NARRATIVE_PROMPT_WITH_CATEGORY.format(category=ad_category.strip())
    return AD_NARRATIVE_PROMPT


# ── 경로 변환 (Windows DB경로 → Linux 컨테이너 경로) ──────────────────────────

def _resolve_path(resource_path: str, ad_type: str) -> str:
    """
    DB의 resource_path가 Windows 절대경로(D:\\ 등)일 경우
    파일명만 추출하여 AD_VIDEO_DIR / AD_IMAGE_DIR 기준으로 재조합한다.
    Linux/컨테이너 환경에서 Windows 경로로 저장된 DB 레코드를 처리하기 위함.
    """
    if len(resource_path) >= 3 and resource_path[1] == ":":
        filename = resource_path.replace("\\", "/").split("/")[-1]
        base_dir = AD_VIDEO_DIR if ad_type == "video_clip" else AD_IMAGE_DIR
        resolved = str(Path(base_dir) / filename)
        logger.debug("Path resolved: %s -> %s", resource_path, resolved)
        return resolved
    return resource_path


# ── 프레임 추출 (video_clip 전용) ─────────────────────────────────────────────

def _extract_video_frame(video_path: str, duration_sec: float | None) -> str | None:
    """
    영상에서 중간 지점(약 33%) 프레임을 JPEG로 추출.

    Args:
        video_path:   영상 파일 경로.
        duration_sec: 영상 길이(초). None이면 3초 지점 사용.

    Returns:
        임시 JPEG 파일 경로, 실패 시 None.
    """
    if not Path(video_path).exists():
        logger.warning("Video not found: %s", video_path)
        return None

    if duration_sec and duration_sec > 0:
        # 33% 지점, 단 최소 1초 / 최대 duration-0.5초
        seek_time = min(max(duration_sec * 0.33, 1.0), max(duration_sec - 0.5, 0.5))
    else:
        seek_time = 3.0

    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-ss", str(seek_time),
                "-i", video_path,
                "-frames:v", "1",
                "-q:v", "2",
                tmp_path,
                "-y",
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        return tmp_path
    except Exception as exc:
        logger.warning("Frame extraction failed for %s: %s", video_path, exc)
        Path(tmp_path).unlink(missing_ok=True)
        return None


# ── Qwen2-VL 광고 분석 ───────────────────────────────────────────────────────

def _analyse_ad(image_path: str, ad_id: str, prompt: str | None = None) -> str:
    """
    단일 이미지로 광고 narrative 생성.

    Args:
        image_path: 분석할 이미지(또는 추출 프레임) 경로.
        ad_id:      로깅용 광고 ID.
        prompt:     사용할 프롬프트. None이면 기본 AD_NARRATIVE_PROMPT 사용.

    Returns:
        정제된 narrative 문자열. 실패 시 "".
    """
    if not Path(image_path).exists():
        logger.warning("[%s] Image not found: %s", ad_id, image_path)
        return FALLBACK_NARRATIVE

    model, processor = _get_model()
    active_prompt = prompt if prompt is not None else AD_NARRATIVE_PROMPT

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image_path},
                {"type": "text",  "text": active_prompt},
            ],
        }
    ]

    text = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )

    try:
        inputs = processor(
            text=[text],
            images=[image_path],
            padding=True,
            return_tensors="pt",
        )
        inputs = {k: v.to(_device) for k, v in inputs.items()}

        with torch.no_grad():
            generated = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS)

        trimmed = generated[0][inputs["input_ids"].shape[-1]:]
        raw = processor.decode(trimmed, skip_special_tokens=True)
        result = _clean_vlm_response(raw)

        logger.info("[%s] narrative (%d chars): %s", ad_id, len(result), result[:100])
        return result

    except Exception as exc:
        logger.error("[%s] VLM inference failed: %s", ad_id, exc)
        return FALLBACK_NARRATIVE


# ── 미처리 광고 조회 ─────────────────────────────────────────────────────────

def _get_unprocessed_ads(limit: int | None = None, force: bool = False) -> list[dict]:
    """
    분석 대상 광고를 반환한다.

    force=False (기본): target_narrative가 NULL이거나 빈 문자열인 광고만 반환.
                        → 멱등성 / Resume 기능 (이미 처리된 광고 Skip).
    force=True        : 모든 광고 반환 (기존 target_narrative 덮어쓰기).
                        → 프롬프트 변경 후 전체 재분석 시 사용.
    """
    if force:
        sql = """
            SELECT ad_id, ad_name, ad_type, resource_path, duration_sec,
                   ad_category
              FROM ad_inventory
             ORDER BY ad_id
        """
    else:
        sql = """
            SELECT ad_id, ad_name, ad_type, resource_path, duration_sec,
                   ad_category
              FROM ad_inventory
             WHERE target_narrative IS NULL
                OR TRIM(target_narrative) = ''
             ORDER BY ad_id
        """
    params = ()
    if limit:
        sql += " LIMIT %s"
        params = (limit,)

    return _db.fetchall(sql, params)


# ── 단일 광고 처리 ────────────────────────────────────────────────────────────

def _process_ad(ad: dict) -> str:
    """
    광고 유형에 따라 이미지/프레임을 준비하고 VLM 분석을 실행.

    Returns:
        생성된 narrative 문자열 (실패 시 "").
    """
    ad_id         = ad["ad_id"]
    ad_type       = ad["ad_type"]
    resource_path = _resolve_path(ad["resource_path"], ad_type)
    duration_sec  = ad.get("duration_sec")

    tmp_frame_path: str | None = None

    try:
        if ad_type == "video_clip":
            tmp_frame_path = _extract_video_frame(resource_path, duration_sec)
            if tmp_frame_path is None:
                logger.warning("[%s] Skipping — frame extraction failed.", ad_id)
                return FALLBACK_NARRATIVE
            image_path = tmp_frame_path

        else:
            # banner / image
            image_path = resource_path
            if not Path(image_path).exists():
                logger.warning("[%s] Image file not found: %s", ad_id, image_path)
                return FALLBACK_NARRATIVE

        prompt = _build_prompt(ad.get("ad_category"))
        return _analyse_ad(image_path, ad_id, prompt=prompt)

    finally:
        # 임시 프레임 파일 정리
        if tmp_frame_path:
            Path(tmp_frame_path).unlink(missing_ok=True)


# ── 메인 ─────────────────────────────────────────────────────────────────────

def run(limit: int | None = None, dry_run: bool = False, force: bool = False) -> None:
    ads = _get_unprocessed_ads(limit, force=force)

    if not ads:
        logger.info("All ads already have target_narrative — nothing to do.")
        return

    logger.info(
        "Found %d ad(s) to process%s%s.",
        len(ads),
        " (force=all)" if force else " (unprocessed only)",
        f", limit={limit}" if limit else "",
    )

    if dry_run:
        logger.info("--dry-run: skipping VLM inference.")
        for ad in ads:
            print(f"  [{ad['ad_type']:12s}] {ad['ad_id']}  — {ad['ad_name']}")
        return

    success = 0
    skipped = 0

    for i, ad in enumerate(ads, 1):
        ad_id = ad["ad_id"]
        logger.info("[%d/%d] Processing %s (%s) ...", i, len(ads), ad_id, ad["ad_type"])

        narrative = _process_ad(ad)

        # narrative가 빈 문자열이면 NULL로 저장 (다음 실행에서 재시도 가능)
        _db.execute(
            "UPDATE ad_inventory SET target_narrative = %s WHERE ad_id = %s",
            (narrative if narrative else None, ad_id),
        )

        if narrative:
            success += 1
        else:
            skipped += 1
            logger.warning("[%s] Stored NULL (will retry on next run).", ad_id)

    logger.info(
        "Done. success=%d, null_stored=%d / total=%d",
        success, skipped, len(ads),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="광고 target_narrative 일괄 생성 (v2.5)"
    )
    parser.add_argument("--limit",   type=int, default=None, help="처리할 최대 광고 수")
    parser.add_argument("--dry-run", action="store_true",    help="분석 없이 미처리 목록만 출력")
    parser.add_argument("--force",   action="store_true",    help="기존 target_narrative 무시하고 전체 재분석")
    args = parser.parse_args()

    run(limit=args.limit, dry_run=args.dry_run, force=args.force)
