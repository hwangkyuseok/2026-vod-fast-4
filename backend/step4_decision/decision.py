"""
Step 4 최종 결정 (decision.py v4.0)
==================================================================
기반: GitHub v2.14 + scoring.py v3.1 및 v4.0 업계 표준 근거 기반 개선

v4.0 변경사항:
  - Brand Safety: GARM Framework 11개 카테고리 기반 복합어 필터 (1글자 오탐 해소)
  - Density: 3-Stage Bucket & Drop
      Golden Zone(이하 0.3): +25, Normal(0.3~0.6): 0, Danger(이상 0.6): DROP
  - 코너 우선순위: TR->TL->BL->BR 에서 BL->BR->TL->TR (lower third 업계 표준)
  - IAB 50% Viewability: 코너 겹침 최소 기준 및 광고 면적의 50%
  - 광고 크기 3-Tier 정규화: Safe(±5%)/Warning(±15%)/Danger(>15% DROP)
  - 광고 도배 방지: 최소 간격 180초, 시간당 6개, 동일 ad_id 중복 금지
  - 모든 상수에 업계 표준 근거 주석 추가 (IAB, GARM, IAS, Nielsen 등)

스코어링 공식 (v4.0):
  [0차] Brand Safety 및 GARM UNSAFE_KEYWORDS 포함 시 Skip
  [0차] 광고 크기 정규화 - Tier 3(>15% 이탈) 시 Skip
  [1차] pre_filter.passes() 및 유사도 + ko-sroberta 코사인유사도 (0.30)
  [2차] video_clip: scene_duration < ad_duration 시 Skip
  ### 슬라이딩 윈도우 + 스코어링 통합 루프 ###
    매 윈도우(1초 단위):
      Danger Zone(density 0.6 이상) 시 skip (인지 과부하)
      코너 겹침 < 광고면적 50% 시 skip (IAB Viewability)
      0~+80   semantic score (Cross-Encoder 또는 ko-sroberta fallback)
      0/+25   density bucket (Golden Zone: +25, Normal: 0)
      +8      침묵 구간 겹침 (transcript+audio 2중 확인)
      +10     ad_category 매칭 보너스
      -> total_score 계산 (범위: 0~123), 최고점 윈도우 선택
  ※ v3.1에 있던 density trend(slope ±3/-5), 발화 gap 보너스(+5),
    scene open 보너스(+5)는 근거 부족으로 v4.0에서 삭제
  ### 코너 배치 ###
    BL->BR->TL->TR 우선순위, IAB 50% 겹침 최소 보장
  ### Dedup ###
    씬별 1개 및 시간 겹침 제거 후 180초 간격 및 시간당 6개 및 ad_id 중복 제거
  [최종] score < MIN_SCORE_TO_KEEP(20) 시 광고 없음 결정

Run:
    python -m step4_decision.decision
"""

import bisect
import logging
from pathlib import Path

import numpy
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import config, db as _db, rabbitmq as mq
from common.logging_setup import setup_logging
from step4_decision import embedding_scorer, pre_filter, cross_encoder_scorer

setup_logging("step4")
logger = logging.getLogger(__name__)

# Cross-Encoder 모델 경로 설정 (config → 환경변수 우선, 없으면 OS별 기본값)
cross_encoder_scorer.set_model_dir(config.CROSS_ENCODER_MODEL_DIR)

### 스코어링 상수 ###
# 근거: Weaviate Hybrid Search 기준 시맨틱 40~60% 권장
#   현재 80/126=63%로 상한 설정. A/B 테스트 범위: 60~80 (50%~70%)
SCORE_SEMANTIC_MAX     = 80    # similarity=1.0 일 때 +80점
# SCORE_SEMANTIC_MIN_SIM: 유형별 차등 적용은 pre_filter.get_threshold()에 위임

# 근거: 방송장비 silence detection 최소 임계값 2초 기반 (Broadcast Tools AES Silence Sentinel)
#   v3.1에서 15점: density 범위(-40~+25=65점)를 침묵이 뒤섞이지 않도록 축소
#   A/B 테스트 대상: 5~15
SCORE_SILENCE_BONUS    = 8     # 침묵 보너스
# 근거: IAS(2023) contextual targeting 2.5배 engagement 향상
#   보조 신호는 총점의 5~15%가 일반적. 10/126=7.9% (A/B 테스트 대상: 5~15)
SCORE_CATEGORY_BONUS   = 10    # ad_category 와 context_narrative 유사도시 0.35
# 카테고리 텍스트가 짧아("화장품" 등) 임베딩 유사도가 높게 나오므로 semantic(0.40)보다 완화
CATEGORY_SIM_THRESHOLD = 0.35  # 카테고리 보너스 적용 최소 유사도

MIN_SCORE_TO_KEEP      = 20    # 이 미만 점수 후보 제거 (A/B 테스트 대상: 20~40)
# Note: pre_filter에서 similarity >= 0.40 강제 시 semantic=0 인 후보는 이미 탈락함.
#   따라서 임계값에 도달하는 후보는 최소 semantic > 0 보장

### Cold Start (시작 N초 광고 금지) ###
# 근거: YouTube mid-roll은 영상 시작 직후 광고 없음, Hulu/Peacock도 초반 몰입 구간 보호.
#   시청자 이탈률은 영상 첫 30초에 가장 높으므로(Nielsen 2019), 초반 몰입을 방해하면 안 됨.
#   영상 길이별 차등:
#     ~20분(애니/숏폼):  30초 후부터 광고 허용
#     ~40분(드라마):     60초 후부터
#     60분+(예능/영화):  90초 후부터
COLD_START_TIERS = [
    (20 * 60,  30),   # 20분 이하 → 30초 금지
    (40 * 60,  60),   # 40분 이하 → 60초 금지
    (float("inf"), 90),  # 40분 초과 → 90초 금지
]

### Cold End (끝 N초 광고 금지) ###
# 근거: 영상 마무리 구간 몰입 보호 — 시작과 동일한 기준 적용
#   영상 길이별 차등:
#     ~20분(애니/숏폼):  끝 30초 전까지
#     ~40분(드라마):     끝 60초 전까지
#     60분+(예능/영화):  끝 90초 전까지
COLD_END_TIERS = [
    (20 * 60,  30),   # 20분 이하 → 끝 30초 금지
    (40 * 60,  60),   # 40분 이하 → 끝 60초 금지
    (float("inf"), 90),  # 40분 초과 → 끝 90초 금지
]

### 광고 도배 방지 상수 ###
# 근거: Hulu 7분 간격, 업계 일반 최소 3분. 보수적 3분(180초). (A/B 테스트 대상: 120~300)
MIN_GAP_SEC        = 180
# 근거: Netflix Basic with Ads 시간당 4~5분(8~10개), 프리미엄 OTT 4~6개 내외. 6개(A/B 대상: 4~8)
MAX_ADS_PER_HOUR   = 6
# 근거: IAB frequency capping, Nielsen(2017) 3회 정점/5회 이후 부정적 효과. 동일 광고 중복 방지
ALLOW_DUPLICATE_AD = False

### Brand Safety ###
# 근거: GARM(Global Alliance for Responsible Media) Brand Safety Framework 11개 카테고리
#   기존 "피" 같은 1글자 필터는 "피자" 등 오탐. 2글자 이상 구체적 복합어로 교체.
#   향후 Gemini LLM 기반 GARM 분류로 고도화 예정.
UNSAFE_KEYWORDS: list[str] = [
    # GARM Floor: 아동 성착취, 테러리즘
    "아동학대", "아동착취", "테러리즘", "테러공격",
    # GARM High: 무기 및 폭력, 자해, 약물
    "총기사사", "폭력행위", "살인사건", "자살시도", "자해행위",
    "혐오발언", "인종차별", "성차별",
    "마약거래", "약물남용",
    # GARM Medium: 선정성, 사망 및 부상
    "성적노출", "선정적장면",
    "사망사고", "중상해", "유해장면",
    # GARM Low: 도박, 논쟁적 주제
    "도박중독", "불법도박",
    "장례식장", "장례예배",  # "장례" 단독 대신 맥락어 조합
]

### 코너 배치 상수 (scoring.py v3.1) ###
DEFAULT_AD_W       = 300   # IAB 표준 배너 기본 너비
DEFAULT_AD_H       = 250   # IAB 표준 배너 기본 높이
DEFAULT_VIDEO_W    = 320   # video_clip 기본 너비 (16:9)
DEFAULT_VIDEO_H    = 180   # video_clip 기본 높이 (16:9)
CORNER_PADDING = 20    # 화면 가장자리 패딩
VIDEO_W        = 1280  # 기준 해상도 너비
VIDEO_H        = 720   # 기준 해상도 높이
# 근거: YouTube/Netflix 오버레이가 하단 1/3(lower third)인 업계 표준
#   하단 2곳(BL/BR)으로 고정 — 위치 일관성 및 몰입 방해 최소화
CORNER_PRIORITY = ["BL", "BR"]


### 코너 배치 함수 (scoring.py v3.1) ###

def _define_corners(
    ad_w: int = DEFAULT_AD_W,
    ad_h: int = DEFAULT_AD_H,
    video_w: int = VIDEO_W,
    video_h: int = VIDEO_H,
) -> dict[str, tuple[int, int]]:
    """4-코너 좌표를 반환한다 (좌상단 기준)."""
    pad = CORNER_PADDING
    return {
        "TR": (video_w - ad_w - pad, pad),
        "TL": (pad, pad),
        "BL": (pad, video_h - ad_h - pad),
        "BR": (video_w - ad_w - pad, video_h - ad_h - pad),
    }


def _corner_overlap_single(
    cx: int, cy: int, ad_w: int, ad_h: int,
    sx: int, sy: int, sw: int, sh: int,
) -> int:
    """코너 직사각형과 단일 프레임 safe_area의 겹침 면적(px)."""
    ox = max(0, min(cx + ad_w, sx + sw) - max(cx, sx))
    oy = max(0, min(cy + ad_h, sy + sh) - max(cy, sy))
    return ox * oy


def _pick_corner_from_frames(
    frames: list[dict],
    ad_w: int = DEFAULT_AD_W,
    ad_h: int = DEFAULT_AD_H,
    video_w: int = VIDEO_W,
    video_h: int = VIDEO_H,
) -> tuple[str, int, int, float] | None:
    """
    프레임별 safe_area를 각 코너와 개별 비교하여 최적 코너를 선택한다.
    코너 좌표는 실제 영상 해상도(video_w × video_h) 기준으로 계산되어
    safe_area(원본 프레임 해상도)와 동일 좌표계에서 비교한다.

    Returns:
        (corner_name, x, y, avg_overlap) 또는 None
    """
    if not frames:
        return None

    corners = _define_corners(ad_w, ad_h, video_w, video_h)
    valid_frames = [
        f for f in frames
        if f.get("safe_area_w") and f.get("safe_area_h")
        and f["safe_area_w"] > 0 and f["safe_area_h"] > 0
    ]
    if not valid_frames:
        return None

    corner_avg: dict[str, float] = {}
    for name in CORNER_PRIORITY:
        cx, cy = corners[name]
        total = sum(
            _corner_overlap_single(
                cx, cy, ad_w, ad_h,
                f["safe_area_x"], f["safe_area_y"],
                f["safe_area_w"], f["safe_area_h"],
            )
            for f in valid_frames
        )
        corner_avg[name] = total / len(valid_frames)

    best_name: str | None = None
    best_avg: float = 0.0
    for name in CORNER_PRIORITY:
        if corner_avg[name] > best_avg:
            best_avg = corner_avg[name]
            best_name = name

    # 근거: IAB/MRC Viewable Ad Impression Guidelines
    #   디스플레이 광고는 50% pixels visible + 1초 연속이어야 유효 노출
    #   오버레이 배너도 디스플레이에 해당하므로 광고 면점의 50% 미만 노출 시 탈락
    ad_area = ad_w * ad_h
    min_overlap = ad_area * 0.50
    if best_name is None or best_avg < min_overlap:
        return None

    cx, cy = corners[best_name]
    return best_name, cx, cy, best_avg


### 광고 크기 정규화 (IAB 3-Tier) ###
# 근거: IAB Medium Rectangle(300x250) 표준 기준
#   OpenRTB 2.6 및 NULL/비정상 값 reject 관행
#   IAB 오버레이 최대 높이: 플레이어 높이의 20%
#   3단계 Tier로 수익 방어(Tier 2)와 브랜드 보호(Tier 3)를 동시에 달성
#
#   Tier 1 (Safe, ±5%):  285~315 x 237~262 사이 원본 유지
#   Tier 2 (Warning, ±15%): 255~345 x 212~287 사이 강제 (300, 250)
#   Tier 3 (Danger, ±15% 초과): 큼 (None, None) 처리 노출 포기

def _normalize_ad_size(
    w: int | None, h: int | None,
    ref_w: int = DEFAULT_AD_W, ref_h: int = DEFAULT_AD_H,
) -> tuple[int | None, int | None]:
    """
    광고 크기를 IAB 기준 300x250 대비 3단계로 검증 및 정규화.

    Returns:
        (w, h)          - Tier 1: 원본 유지
        (ref_w, ref_h)  - Tier 2: 강제 보정
        (None, None)    - Tier 3: 노출 포함 제외
    """
    if not w or not h or w <= 0 or h <= 0:
        logger.debug("[AD_SIZE] NULL/0 시 fallback (%d, %d)", ref_w, ref_h)
        return ref_w, ref_h

    w_ratio = abs(w - ref_w) / ref_w
    h_ratio = abs(h - ref_h) / ref_h

    if w_ratio <= 0.05 and h_ratio <= 0.05:
        # Tier 1 (Safe): 원본 유지
        return w, h
    elif w_ratio <= 0.15 and h_ratio <= 0.15:
        # Tier 2 (Warning): 강제 보정
        logger.info(
            "[AD_SIZE][TIER2] Warning: (%d, %d) 시 forced (%d, %d)",
            w, h, ref_w, ref_h,
        )
        return ref_w, ref_h
    else:
        # Tier 3 (Danger): 노출 포기
        logger.warning(
            "[AD_SIZE][TIER3] Danger: (%d, %d) deviates >15%% from (%d, %d) 시 DROP",
            w, h, ref_w, ref_h,
        )
        return None, None


### DB 헬퍼 ###

def _update_job_status(job_id: str, status: str, error: str | None = None) -> None:
    _db.execute(
        "UPDATE job_history SET status=%s, error_message=%s, updated_at=NOW() WHERE job_id=%s",
        (status, error, job_id),
    )


### 캐시 기반 헬퍼 (GitHub v2.13 캐싱 + scoring.py v3.1 침묵 로직) ###

def _get_scene_frames_cached(
    all_frames: list[dict],
    scene_start: float,
    scene_end: float,
) -> list[dict]:
    """v2.13: prefetch한 전체 프레임 리스트에서 해당 범위 프레임을 이진탐색."""
    timestamps = [float(f["timestamp_sec"]) for f in all_frames]
    lo = bisect.bisect_left(timestamps, scene_start)
    hi = bisect.bisect_right(timestamps, scene_end)
    return all_frames[lo:hi]


def _check_silence_from_cache(
    cached_transcripts: list[dict],
    has_any_transcript: bool,
    cached_audio: list[dict],
    window_start: float,
    window_end: float,
) -> bool:
    """
    scoring.py v3.1의 transcript+audio 2중 침묵 판단 (DB 호출 0건).

    1차 Whisper transcript 기반 해당 윈도우 내 발화 사이 2초 이상 공백이면 침묵
    2차 transcript 없지만 job 전체에는 있으면 해당 침묵
    3차 analysis_audio(librosa) fallback
    """
    overlapping = [
        t for t in cached_transcripts
        if float(t["end_sec"]) > window_start and float(t["start_sec"]) < window_end
    ]
    if overlapping:
        prev = window_start
        for t in overlapping:
            gap = float(t["start_sec"]) - prev
            if gap >= 2.0:
                return True
            prev = max(prev, float(t["end_sec"]))
        if window_end - prev >= 2.0:
            return True
        return False

    if has_any_transcript:
        return True

    for a in cached_audio:
        if float(a["silence_start_sec"]) < window_end and float(a["silence_end_sec"]) > window_start:
            return True
    return False


### 통합 스코어링 (v4.0 통합 방식) ###

def _score_candidate(
    candidate: dict,
    job_id: str,
    precomputed_similarity: float | None = None,
    frames_cache: list[dict] | None = None,
    transcript_cache: list[dict] | None = None,
    has_any_transcript: bool = False,
    silence_cache: list[dict] | None = None,
    video_w: int = VIDEO_W,
    video_h: int = VIDEO_H,
) -> tuple[int, dict | None, float]:
    """
    v4.0 통합 스코어링 + GitHub DB 캐싱.

    Returns:
        (score, window, similarity)
    """
    context_narrative = (candidate.get("context_narrative") or "").strip()
    target_narrative  = (candidate.get("target_narrative") or "").strip()
    scene_start       = float(candidate["scene_start_sec"])
    scene_end         = float(candidate["scene_end_sec"])
    scene_duration    = float(candidate["scene_duration"])
    ad_dur            = candidate.get("ad_duration_sec")
    ad_type           = candidate.get("ad_type", "banner")

    ### 0차 필터: Brand Safety ###
    if context_narrative:
        for kw in UNSAFE_KEYWORDS:
            if kw in context_narrative:
                logger.info(
                    "[SAFETY][SKIP] scene=%.1f~%.1f keyword=%s",
                    scene_start, scene_end, kw,
                )
                return 0, None, 0.0

    ### 1차 필터: pre_filter ###
    # precomputed가 있으면 outer pre_filter를 이미 통과한 후보 → threshold 체크 생략
    if precomputed_similarity is not None:
        similarity = precomputed_similarity
    else:
        passed, similarity = pre_filter.passes(candidate, None)
        if not passed:
            return 0, None, similarity

    ### 2차 필터: 물리적 수용 가능성 ###
    if ad_type == "video_clip" and ad_dur is not None:
        if scene_duration < ad_dur:
            return 0, None, similarity
        window_duration = ad_dur
    else:
        window_duration = min(
            ad_dur if ad_dur is not None else config.AD_BANNER_DURATION_SEC,
            scene_duration,
        )

    ### 루프 전 고정 점수 사전 계산 ###
    # 유형별 동적 threshold로 스케일링 하한 결정
    semantic_min_sim = pre_filter.get_threshold(candidate)
    base_semantic = 0
    if similarity >= semantic_min_sim:
        scaled = (similarity - semantic_min_sim) / (1.0 - semantic_min_sim)
        base_semantic = int(scaled * SCORE_SEMANTIC_MAX)

    category_bonus = 0
    ad_category = (candidate.get("ad_category") or "").strip()
    if ad_category and context_narrative and embedding_scorer.is_available():
        cat_sim = embedding_scorer.compute_similarity(context_narrative, ad_category)
        if cat_sim >= CATEGORY_SIM_THRESHOLD:
            category_bonus = SCORE_CATEGORY_BONUS

    ### 프레임 데이터 (캐시 활용) ###
    if frames_cache is not None:
        frames = _get_scene_frames_cached(frames_cache, scene_start, scene_end)
    else:
        frames = _db.fetchall(
            """SELECT timestamp_sec, safe_area_x, safe_area_y, safe_area_w, safe_area_h,
                      object_density
                 FROM analysis_vision_context
                WHERE job_id = %s AND timestamp_sec >= %s AND timestamp_sec <= %s
                ORDER BY timestamp_sec""",
            (job_id, scene_start, scene_end),
        )
    if not frames:
        return 0, None, similarity

    ### 침묵 데이터 (캐시 필터) ###
    scene_transcripts = [
        t for t in (transcript_cache or [])
        if float(t["end_sec"]) > scene_start and float(t["start_sec"]) < scene_end
    ]
    scene_audio = [
        a for a in (silence_cache or [])
        if float(a["silence_start_sec"]) < scene_end and float(a["silence_end_sec"]) > scene_start
    ]

    _is_video = candidate.get("ad_type", "banner") == "video_clip"
    _ref_w = DEFAULT_VIDEO_W if _is_video else DEFAULT_AD_W
    _ref_h = DEFAULT_VIDEO_H if _is_video else DEFAULT_AD_H
    ad_w, ad_h = _normalize_ad_size(
        candidate.get("width"), candidate.get("height"),
        ref_w=_ref_w, ref_h=_ref_h,
    )
    if ad_w is None or ad_h is None:
        return 0, None, 0.0

    ### 슬라이딩 윈도우 루프 ###
    best_window: dict | None = None
    best_score: int = -999
    t = scene_start

    while t + window_duration <= scene_end + 0.5:
        window_end = t + window_duration
        window_frames = [f for f in frames if t <= f["timestamp_sec"] <= window_end]

        if not window_frames:
            t += 1.0
            continue

        avg_density = sum(f["object_density"] or 0.0 for f in window_frames) / len(window_frames)

        ### 코너 배치 판단 (v4.0 Viewability 보장) ###
        corner_result = _pick_corner_from_frames(window_frames, ad_w, ad_h, video_w, video_h)
        if corner_result is None:
            t += 1.0
            continue

        corner_name, corner_x, corner_y, corner_overlap = corner_result

        ### 점수 계산 (v4.0 3-Stage Bucket + density trend) ###
        if avg_density >= 0.6:
            t += 1.0
            continue

        total = base_semantic + category_bonus
        if avg_density <= 0.3:
            total += 25
        # 0.3~0.6 사이는 0점

        # 침묵 보너스
        has_silence = _check_silence_from_cache(
            scene_transcripts, has_any_transcript, scene_audio, t, window_end,
        )
        if has_silence:
            total += SCORE_SILENCE_BONUS

        if total > best_score:
            best_score = total
            if scene_duration >= 30.0:
                _delay = 2.5
            elif scene_duration >= 10.0:
                _delay = 1.5
            else:
                _delay = 0.5
            best_window = {
                "start_sec":       t + _delay,
                "avg_density":     avg_density,
                "safe_area_px":    int(corner_overlap),
                "corner_name":     corner_name,
                "corner_x":        corner_x,
                "corner_y":        corner_y,
                "corner_overlap":  corner_overlap,
                "silence_overlap": has_silence,
            }

        t += 1.0

    if best_window is None:
        return 0, None, similarity

    return best_score, best_window, similarity


### Dedup (v4.0 업계 표준) ###

def _pick_best_and_deduplicate(scored: list[dict], duration_sec: float = 0.0) -> list[dict]:
    """
    1. Per unique scene_start_sec: keep only the highest-scoring ad.
    2. Sort by overlay_start_time_sec, then remove time-overlapping windows.
    3. 영상 길이 기반 동적 최소 간격 적용.
    4. 시간당 최대 광고 수(MAX_ADS_PER_HOUR) 제한.
    5. ad_id 중복 방지.
    """
    # Step 1: 씬별 최고점 광고 1개
    best: dict[float, dict] = {}
    for s in scored:
        key = s["scene_start_sec"]
        if key not in best or s["score"] > best[key]["score"]:
            best[key] = s

    candidates = sorted(
        (v for v in best.values() if v["score"] >= MIN_SCORE_TO_KEEP),
        key=lambda x: x["overlay_start_time_sec"],
    )

    # Step 2: 오버레이 시간 겹침 제거
    deduped: list[dict] = []
    for c in candidates:
        start = c["overlay_start_time_sec"]
        if not deduped:
            deduped.append(c)
            continue
        prev     = deduped[-1]
        prev_end = prev["overlay_start_time_sec"] + prev["overlay_duration_sec"]
        if start >= prev_end:
            deduped.append(c)
        elif c["score"] > prev["score"]:
            deduped[-1] = c

    # Step 3a: Cold Start — 영상 시작 N초 광고 금지
    cold_start_sec = 30  # 기본값
    if duration_sec > 0:
        for tier_max, tier_cold in COLD_START_TIERS:
            if duration_sec <= tier_max:
                cold_start_sec = tier_cold
                break
    deduped = [c for c in deduped if c["overlay_start_time_sec"] >= cold_start_sec]
    logger.info(
        "[COLD_START] duration_sec=%.1f → 시작 %d초 금지 → %d개 남음",
        duration_sec, cold_start_sec, len(deduped),
    )

    # Step 3a-2: Cold End — 영상 끝 N초 광고 금지
    if duration_sec > 0:
        cold_end_sec = 10  # 기본값
        for tier_max, tier_cold in COLD_END_TIERS:
            if duration_sec <= tier_max:
                cold_end_sec = tier_cold
                break
        deduped = [c for c in deduped if c["overlay_start_time_sec"] <= duration_sec - cold_end_sec]
        logger.info("[COLD_END] duration_sec=%.1f → 끝 %d초 금지 → %d개 남음", duration_sec, cold_end_sec, len(deduped))

    # Step 3b: 영상 길이 기반 동적 최소 광고 간격
    if duration_sec > 0:
        dynamic_interval = min(300, max(60, 60 * (int(duration_sec // 1800) + 1)))
    else:
        dynamic_interval = MIN_GAP_SEC  # duration 미획득시 기본 180초
    logger.info(
        "[INTERVAL] duration_sec=%.1f → min_ad_interval=%ds",
        duration_sec, dynamic_interval,
    )

    result: list[dict] = []
    for c in deduped:
        start = c["overlay_start_time_sec"]
        if result:
            prev_start = result[-1]["overlay_start_time_sec"]
            if start - prev_start < dynamic_interval:
                if c["score"] > result[-1]["score"]:
                    result[-1] = c
                continue

        # 시간당 최대 광고 수 체크
        hour_bucket = int(start // 3600)
        ads_in_hour = sum(1 for r in result if int(r["overlay_start_time_sec"] // 3600) == hour_bucket)
        if ads_in_hour >= MAX_ADS_PER_HOUR:
            logger.info(
                "[DENSITY] skip ad=%.1fs — hour %d already has %d ads (max %d)",
                start, hour_bucket, ads_in_hour, MAX_ADS_PER_HOUR,
            )
            continue

        result.append(c)

    # Step 4: 중복 광고 필터
    if not ALLOW_DUPLICATE_AD:
        seen = set()
        final = []
        for c in result:
            aid = c.get("ad_id")
            if aid not in seen:
                seen.add(aid)
                final.append(c)
        return final

    return result


### INSERT ###

def _insert_decision_results(job_id: str, results: list[dict]) -> None:
    with _db.cursor() as cur:
        cur.execute("DELETE FROM decision_result WHERE job_id = %s", (job_id,))
        for r in results:
            cur.execute(
                """
                INSERT INTO decision_result
                    (job_id, ad_id,
                     overlay_start_time_sec, overlay_duration_sec,
                     coordinates_x, coordinates_y,
                     coordinates_w, coordinates_h,
                     score,
                     similarity_score, scene_duration_sec, avg_density)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    job_id, r["ad_id"],
                    float(r["overlay_start_time_sec"]), float(r["overlay_duration_sec"]),
                    r.get("coordinates_x"), r.get("coordinates_y"),
                    r.get("coordinates_w"), r.get("coordinates_h"),
                    int(r["score"]),
                    r.get("similarity_score"), r.get("scene_duration_sec"), r.get("avg_density"),
                ),
            )
    logger.info("Inserted %d decision result(s) for job %s", len(results), job_id)


### 메인 실행 ###

def run(job_id: str, candidates: list[dict], duration_sec: float = 0.0) -> None:
    _update_job_status(job_id, "deciding")
    try:
        # ── 실제 영상 해상도 조회 (safe_area와 동일 좌표계) ──────────────
        preproc = _db.fetchone(
            "SELECT width, height FROM video_preprocessing_info WHERE job_id = %s",
            (job_id,),
        )
        if preproc and preproc["width"] and preproc["height"]:
            video_w = int(preproc["width"])
            video_h = int(preproc["height"])
        else:
            video_w = VIDEO_W
            video_h = VIDEO_H
            logger.warning("[%s] video_preprocessing_info 없음 — 기본 %dx%d 사용", job_id, video_w, video_h)

        sim_lookup: dict[tuple[str, str], float] = {}
        ctx_to_desire: dict[str, str] = {}
        desire_lookup: dict[tuple[str, str], float] = {}

        ### 1단계: ko-sroberta pre-filter + Desire 블렌딩 ###
        EMBED_TOP_K_PER_SCENE = 30
        embed_lookup: dict[tuple[str, str], float] = {}

        if candidates and embedding_scorer.is_available():
            unique_ctx = list(dict.fromkeys(
                c.get("context_narrative") or "" for c in candidates
                if c.get("context_narrative") and c.get("target_narrative")
            ))
            unique_tgt = list(dict.fromkeys(
                c.get("target_narrative") or "" for c in candidates
                if c.get("context_narrative") and c.get("target_narrative")
            ))

            if unique_ctx and unique_tgt:
                sim_matrix = embedding_scorer.batch_similarity_matrix(unique_ctx, unique_tgt)
                ctx_idx = {t: i for i, t in enumerate(unique_ctx)}
                tgt_idx = {t: i for i, t in enumerate(unique_tgt)}
                for c in candidates:
                    ctx = c.get("context_narrative") or ""
                    tgt = c.get("target_narrative") or ""
                    if ctx and tgt:
                        embed_lookup[(ctx, tgt)] = float(sim_matrix[ctx_idx[ctx], tgt_idx[tgt]])

                logger.info("[%s] Batch similarity matrix: %d ctx × %d tgt", job_id, len(unique_ctx), len(unique_tgt))

                # Desire 블렌딩 (0.7×context + 0.3×desire)
                for c in candidates:
                    ctx = c.get("context_narrative") or ""
                    desire = c.get("desire") or ""
                    if ctx and desire:
                        ctx_to_desire[ctx] = desire
                unique_desire = list(dict.fromkeys(v for v in ctx_to_desire.values() if v))
                if unique_desire:
                    desire_sim_matrix = embedding_scorer.batch_similarity_matrix(unique_desire, unique_tgt)
                    desire_idx_map = {t: i for i, t in enumerate(unique_desire)}
                    for c in candidates:
                        ctx = c.get("context_narrative") or ""
                        tgt = c.get("target_narrative") or ""
                        if not (ctx and tgt):
                            continue
                        desire = ctx_to_desire.get(ctx, "")
                        if desire and desire in desire_idx_map and tgt in tgt_idx:
                            ctx_sim = embed_lookup.get((ctx, tgt), 0.0)
                            d_sim = float(desire_sim_matrix[desire_idx_map[desire], tgt_idx[tgt]])
                            embed_lookup[(ctx, tgt)] = 0.4 * ctx_sim + 0.6 * d_sim
                            desire_lookup[(ctx, tgt)] = d_sim
                    logger.info(
                        "[%s] Desire blending: %d desires x %d ads blended into embed_lookup",
                        job_id, len(unique_desire), len(unique_tgt),
                    )

            before = len(candidates)

            # 임계값 통과 후보 수집
            filtered = []
            for c in candidates:
                ctx = c.get("context_narrative") or ""
                tgt = c.get("target_narrative") or ""
                precomputed = embed_lookup.get((ctx, tgt))
                passed, _ = pre_filter.passes(c, precomputed)
                if passed:
                    filtered.append(c)

            # Per-scene Top-K: 씬별 상위 EMBED_TOP_K_PER_SCENE개만 유지
            from collections import defaultdict
            scene_buckets: dict[str, list[tuple[float, dict]]] = defaultdict(list)
            for c in filtered:
                ctx = c.get("context_narrative") or ""
                tgt = c.get("target_narrative") or ""
                sim = embed_lookup.get((ctx, tgt), 0.0)
                scene_buckets[ctx].append((sim, c))

            candidates = []
            for ctx, items in scene_buckets.items():
                items.sort(key=lambda x: x[0], reverse=True)
                candidates.extend(c for _, c in items[:EMBED_TOP_K_PER_SCENE])

            logger.info(
                "[%s] pre-filter: %d → %d (threshold) → %d (top-%d/scene)",
                job_id, before, len(filtered), len(candidates), EMBED_TOP_K_PER_SCENE,
            )

        ### 2단계: Cross-Encoder 배치 + 씬별 Top-3 ###
        CE_TOP_K_PER_SCENE = 3
        if candidates:
            pairs = [
                (c.get("context_narrative") or "", c.get("target_narrative") or "")
                for c in candidates
                if c.get("context_narrative") and c.get("target_narrative")
            ]
            if pairs:
                unique_pairs = list(dict.fromkeys(pairs))

                if cross_encoder_scorer.is_available():
                    scores = cross_encoder_scorer.batch_score(unique_pairs)
                    sim_lookup = dict(zip(unique_pairs, scores))
                    logger.info(
                        "[%s] Cross-Encoder batch: %d pair(s) scored.",
                        job_id, len(sim_lookup),
                    )
                    scored_list = [
                        (c, sim_lookup.get(
                            (c.get("context_narrative") or "", c.get("target_narrative") or ""), 0.0
                        ))
                        for c in candidates
                    ]
                    from collections import defaultdict as _dd
                    ce_buckets: dict[str, list] = _dd(list)
                    for c, sim in scored_list:
                        ce_buckets[c.get("context_narrative") or ""].append((sim, c))
                    candidates = []
                    for items in ce_buckets.values():
                        items.sort(key=lambda x: x[0], reverse=True)
                        candidates.extend(c for _, c in items[:CE_TOP_K_PER_SCENE])
                    logger.info(
                        "[%s] CE Top-%d/scene: %d씬 %d 후보 (from %d).",
                        job_id, CE_TOP_K_PER_SCENE, len(ce_buckets),
                        len(candidates), len(scored_list),
                    )
                elif not sim_lookup:
                    # CE 없으면 ko-sroberta fallback
                    unique_ctx = list(dict.fromkeys(p[0] for p in unique_pairs))
                    unique_tgt = list(dict.fromkeys(p[1] for p in unique_pairs))
                    fb_matrix = embedding_scorer.batch_similarity_matrix(unique_ctx, unique_tgt)
                    fb_ctx_idx = {t: i for i, t in enumerate(unique_ctx)}
                    fb_tgt_idx = {t: i for i, t in enumerate(unique_tgt)}
                    for ctx, tgt in unique_pairs:
                        sim_lookup[(ctx, tgt)] = float(fb_matrix[fb_ctx_idx[ctx], fb_tgt_idx[tgt]])
                    logger.info(
                        "[%s] Fallback ko-sroberta batch: %d pair(s) pre-computed.",
                        job_id, len(sim_lookup),
                    )

        ### DB prefetch ###
        frames_cache = _db.fetchall(
            """SELECT timestamp_sec,
                      safe_area_x, safe_area_y, safe_area_w, safe_area_h,
                      object_density
                 FROM analysis_vision_context
                WHERE job_id = %s
                ORDER BY timestamp_sec""",
            (job_id,),
        )
        silence_cache = _db.fetchall(
            "SELECT * FROM analysis_audio WHERE job_id = %s", (job_id,),
        )
        transcript_cache = _db.fetchall(
            "SELECT * FROM analysis_transcript WHERE job_id = %s ORDER BY start_sec",
            (job_id,),
        )
        has_any_transcript = len(transcript_cache) > 0
        logger.info(
            "[%s] Prefetched %d vision frames, %d silence intervals, %d transcripts.",
            job_id, len(frames_cache), len(silence_cache), len(transcript_cache),
        )

        ### 스코어링 루프 ###
        scored_candidates = []
        for c in candidates:
            ctx = c.get("context_narrative") or ""
            tgt = c.get("target_narrative") or ""
            emb_val = embed_lookup.get((ctx, tgt))
            sl_val = sim_lookup.get((ctx, tgt))
            precomputed = emb_val if emb_val is not None else sl_val

            score, window, similarity = _score_candidate(
                c, job_id,
                precomputed_similarity=precomputed,
                frames_cache=frames_cache,
                transcript_cache=transcript_cache,
                has_any_transcript=has_any_transcript,
                silence_cache=silence_cache,
                video_w=video_w,
                video_h=video_h,
            )

            if score <= 0 or window is None:
                continue

            ad_dur  = c.get("ad_duration_sec") or config.AD_BANNER_DURATION_SEC
            ad_type = c.get("ad_type", "banner")
            overlay_dur = ad_dur if ad_type == "video_clip" else min(ad_dur, c["scene_duration"])

            _is_vid = c.get("ad_type", "banner") == "video_clip"
            norm_w, norm_h = _normalize_ad_size(
                c.get("width"), c.get("height"),
                ref_w=DEFAULT_VIDEO_W if _is_vid else DEFAULT_AD_W,
                ref_h=DEFAULT_VIDEO_H if _is_vid else DEFAULT_AD_H,
            )
            scored_candidates.append({
                **c,
                "score":                  score,
                "similarity_score":       float(similarity),
                "scene_duration_sec":     float(c["scene_duration"]),
                "avg_density":            float(window["avg_density"]),
                "overlay_start_time_sec": float(window["start_sec"]),
                "overlay_duration_sec":   float(overlay_dur),
                "coordinates_x":          window.get("corner_x", CORNER_PADDING),
                "coordinates_y":          window.get("corner_y", CORNER_PADDING),
                "coordinates_w":          norm_w,
                "coordinates_h":          norm_h,
                "corner_name":            window.get("corner_name", "BL"),
            })

        ### Dedup + INSERT ###
        best = _pick_best_and_deduplicate(scored_candidates, duration_sec=duration_sec)
        _insert_decision_results(job_id, best)
        _update_job_status(job_id, "complete")
        logger.info("[%s] Step-4 complete — %d overlays decided.", job_id, len(best))

    except Exception as exc:
        _update_job_status(job_id, "failed", str(exc))
        logger.exception("[%s] Step-4 failed: %s", job_id, exc)
        raise

def _on_message(payload: dict) -> None:
    job_id = payload["job_id"]
    from step3_persistence.pipeline import build_candidates
    candidates = build_candidates(job_id)
    # 영상 길이 조회 → 동적 최소 광고 간격 계산용
    row = _db.fetchone(
        "SELECT duration_sec FROM video_preprocessing_info WHERE job_id = %s",
        (job_id,),
    )
    duration_sec = float(row["duration_sec"]) if row and row.get("duration_sec") else 0.0
    run(job_id, candidates, duration_sec=duration_sec)

if __name__ == "__main__":
    mq.consume(config.QUEUE_STEP4, _on_message)
