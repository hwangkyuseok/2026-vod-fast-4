"""
프롬프트 비교 테스트 — 기존 vs 신규
기존 DB 결과와 새 프롬프트 결과를 나란히 비교합니다.
"""
import warnings
warnings.filterwarnings('ignore')

import torch
from pathlib import Path
from transformers import AutoProcessor, Qwen2VLForConditionalGeneration

# ── 설정 ──────────────────────────────────────────────────────
MODEL_ID = "Qwen/Qwen2-VL-2B-Instruct"
JOB_DIR = Path(r"D:\20.WORKSPACE\2026_VOD_FAST_4\storage\jobs\09cafd6c-05cf-481c-882e-5a2e7062ae5c\frames")

# 테스트할 프레임 (앞/중간/뒤에서 각 1장씩 = 3장)
TEST_FRAMES = [
    JOB_DIR / "frame_000010.jpg",   # 초반
    JOB_DIR / "frame_000280.jpg",   # 중반
    JOB_DIR / "frame_000500.jpg",   # 후반
]

# ── 기존 프롬프트 (AS-IS) ─────────────────────────────────────
OLD_PROMPT_TEMPLATE = (
    "당신은 한국 TV 드라마 장면을 분석하여 광고 매칭에 활용할 컨텍스트를 생성하는 전문가입니다.\n\n"
    "{context}\n\n"
    "1~2문장으로 다음을 한국어로 설명하세요:\n"
    "- 등장인물과 그들이 하는 행동\n"
    "- 감정적 분위기\n"
    "- 인물들의 욕구나 필요\n\n"
    "사실에 근거하여 구체적으로 작성하세요. 광고 카테고리나 브랜드명은 언급하지 마세요.\n\n"
    "반드시 한국어로만 답하세요. Do not use English. 영어 사용 금지."
)

# ── 신규 프롬프트 (TO-BE) ─────────────────────────────────────
NEW_PROMPT_TEMPLATE = (
    "당신은 한국 TV 드라마 장면을 분석하는 전문가입니다.\n"
    "아래 장면 정보를 바탕으로, 정확히 3가지 항목을 분석하세요.\n\n"
    "{context}\n\n"
    "### 출력 규칙\n"
    "- 각 항목은 반드시 1문장으로 작성하세요.\n"
    "- 화면에 보이는 사실만 근거로 작성하세요. 추측하지 마세요.\n"
    "- 같은 내용을 반복하지 마세요. 각 항목은 서로 다른 정보를 담아야 합니다.\n"
    "- 광고, 브랜드, 제품명은 절대 언급하지 마세요.\n"
    "- 반드시 한국어로만 작성하세요.\n\n"
    "### 출력 형식 (이 형식을 정확히 따르세요)\n"
    "상황: [이 장면에서 누가 어디서 무엇을 하고 있는지 구체적으로 서술]\n"
    "감정: [이 장면의 감성적 분위기를 서술]\n"
    "욕구: [이 장면의 인물 또는 시청자가 느낄 수 있는 니즈나 욕구를 서술]"
)

# ── 모델 로드 ─────────────────────────────────────────────────
print("=" * 60)
print("  Qwen2-VL 프롬프트 비교 테스트")
print("=" * 60)

device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"\n디바이스: {device}")
print("모델 로딩 중...")

model = Qwen2VLForConditionalGeneration.from_pretrained(
    MODEL_ID,
    torch_dtype=torch.float16 if device == "cuda" else torch.float32,
    device_map="auto" if device == "cuda" else None,
)
if device == "cpu":
    model = model.to("cpu")
processor = AutoProcessor.from_pretrained(MODEL_ID)
print("모델 로딩 완료!\n")


def run_inference(frame_path: str, prompt: str) -> str:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": frame_path},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    text = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    inputs = processor(
        text=[text], images=[frame_path], padding=True, return_tensors="pt"
    )
    inputs = {k: v.to(device) for k, v in inputs.items()}

    with torch.no_grad():
        generated = model.generate(**inputs, max_new_tokens=160)

    trimmed = generated[0][inputs["input_ids"].shape[-1]:]
    return processor.decode(trimmed, skip_special_tokens=True).strip()


# ── 테스트 실행 ───────────────────────────────────────────────
context_dummy = "(대사 없음)"

for i, frame_path in enumerate(TEST_FRAMES, 1):
    if not frame_path.exists():
        print(f"\n⚠️ 프레임 없음: {frame_path}")
        continue

    print(f"\n{'='*60}")
    print(f"  테스트 {i}/3 — {frame_path.name}")
    print(f"{'='*60}")

    old_prompt = OLD_PROMPT_TEMPLATE.format(context=context_dummy)
    new_prompt = NEW_PROMPT_TEMPLATE.format(context=context_dummy)

    print("\n🔴 [기존 프롬프트 결과]")
    print("-" * 40)
    old_result = run_inference(str(frame_path), old_prompt)
    print(old_result)

    print("\n🟢 [신규 프롬프트 결과]")
    print("-" * 40)
    new_result = run_inference(str(frame_path), new_prompt)
    print(new_result)

print(f"\n{'='*60}")
print("  테스트 완료!")
print("='*60")
