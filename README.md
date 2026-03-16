# VOD Dynamic Ad Overlay System

비디오 문맥 분석 기반 동적 광고 오버레이 시스템 (v2.10)

---

## 버전 히스토리

| 버전 | 주요 변경 |
|------|-----------|
| v2.0 | 기본 키워드 스코어링 |
| v2.2 | Semantic embedding (target_mood 앙상블) |
| v2.5 | target_narrative 1:1 매칭, Scene 분절, analyse_scene_context() |
| v2.6 | Scene-driven 전환 — 씬 내 슬라이딩 윈도우로 최적 타임스탬프 확정 |
| v2.7 | 맥락 부적합 광고 억제 (NARRATIVE_THRESHOLD 0.50, MIN_SCORE_TO_KEEP 20) |
| v2.8 | YOLOv8l 교체, batch_similarity_matrix 배치 연산, dialogue_segmenter |
| v2.9 | target_mood 컬럼 및 레거시 코드 완전 제거 |
| v2.10 | ad_category 카테고리 매칭 보너스, analyze_ad_narrative 카테고리 프롬프트 |

---

## 아키텍처 개요

```
[광고 데이터 준비 (사전 작업)]
  tvcf_downloader.py       → ad_inventory (video_clip, ad_category)
  tvcf_print_downloader.py → ad_inventory (banner, ad_category)
  analyze_ad_narrative.py  → ad_inventory.target_narrative (Qwen2-VL 4차원 분석)

[파이프라인]
[FastAPI] ──POST /jobs──► [RabbitMQ: step1]
                                │
                    ┌───────────▼────────────┐
                    │  Step-1 Preprocessing  │  ffmpeg 프레임/오디오 추출
                    │                        │  scenedetect 시각적 컷 감지
                    └───────────┬────────────┘
                                │ [step2]
                    ┌───────────▼────────────┐
                    │  Step-2 Analysis       │  YOLOv8l (safe area, 밀집도)
                    │                        │  Qwen2-VL (씬 서술 narrative)
                    │                        │  Whisper STT + dialogue_segmenter
                    │                        │  librosa (묵음 구간 감지)
                    └───────────┬────────────┘
                                │ [step3]
                    ┌───────────▼────────────┐
                    │  Step-3 Ad Matching    │  씬(analysis_scene) × ad_inventory
                    │                        │  후보 페어 생성 → QUEUE_STEP4
                    └───────────┬────────────┘
                                │ [step4]
                    ┌───────────▼────────────┐
                    │  Step-4 Scoring        │  narrative 유사도 + 카테고리 보너스
                    │                        │  슬라이딩 윈도우 → decision_result
                    └────────────────────────┘
                                │
              [FastAPI] GET /overlay/{job_id}
                                │
              [Next.js Player]  실시간 오버레이 렌더링
```

---

## 디렉토리 구조

```
2026_VOD_FAST_4/
├── backend/
│   ├── common/                   # 공통 유틸 (DB, RabbitMQ, config)
│   ├── step1_preprocessing/      # ffmpeg 추출, scenedetect
│   ├── step2_analysis/           # YOLOv8l, Qwen2-VL, Whisper, librosa
│   │   ├── consumer.py           # Step-2 메인 오케스트레이터
│   │   ├── vision_yolo.py        # YOLOv8l safe area 분석
│   │   ├── vision_qwen.py        # Qwen2-VL 씬 서술
│   │   ├── audio_analysis.py     # Whisper STT + librosa 묵음
│   │   └── dialogue_segmenter.py # 의미 단위 씬 경계 감지
│   ├── step3_persistence/        # 씬 × 광고 후보 페어 생성
│   ├── step4_decision/           # 스코어링 → decision_result 저장
│   │   ├── scoring.py            # 메인 스코어링 파이프라인
│   │   └── embedding_scorer.py   # sentence-transformers 유사도
│   ├── step5_api/                # FastAPI REST 서버
│   ├── init_schema.sql           # DB DDL (전체 스키마)
│   ├── init_db.py                # DB 초기화
│   ├── populate_ad_inventory.py  # 로컬 광고 파일 등록
│   └── analyze_ad_narrative.py   # Qwen2-VL 광고 narrative 생성
└── frontend/                     # Next.js 14 App
    └── src/
        ├── app/
        │   ├── page.tsx               # 홈 (작업 제출 / 플레이어 접근)
        │   └── player/[jobId]/        # VOD 플레이어 페이지
        ├── components/
        │   ├── VideoPlayer.tsx        # HTML5 비디오 + 오버레이 렌더링
        │   └── AdOverlay.tsx          # 단일 광고 오버레이 (영상/이미지)
        └── types/overlay.ts           # TypeScript 타입 정의
```

---

## 시작 가이드

### 1. Python 환경 설정

```bash
cd backend
pip install -r requirements.txt
```

### 2. DB 스키마 초기화

```bash
python init_db.py
```

### 3. 광고 인벤토리 등록

```bash
# 로컬 파일 기반 등록
python populate_ad_inventory.py

# TVCF 크롤링 다운로드 (2026_VOD_FAST_3/TV_CF/)
python tvcf_downloader.py        # 영상 광고
python tvcf_print_downloader.py  # 인쇄 광고(배너)
```

### 4. 광고 narrative 생성 (Qwen2-VL 사전 분석)

```bash
# 미처리 광고만 분석 (Resume 가능)
python analyze_ad_narrative.py

# 전체 재분석 (프롬프트 변경 시)
python analyze_ad_narrative.py --force

# 테스트 (N개만)
python analyze_ad_narrative.py --limit 5
```

> `ad_category`가 채워진 광고는 카테고리 컨텍스트가 포함된 프롬프트로 분석됩니다.
> `ad_category`가 NULL이면 기본 프롬프트를 사용합니다 (graceful degradation).

### 5. 파이프라인 서비스 시작 (터미널 4개)

```bash
# 터미널 1 — Step 1 (전처리)
python -m step1_preprocessing.pipeline --consume

# 터미널 2 — Step 2 (분석)
python -m step2_analysis.consumer

# 터미널 3 — Step 3 (매칭)
python -m step3_persistence.pipeline

# 터미널 4 — Step 4 (스코어링)
python -m step4_decision.scoring
```

### 6. FastAPI 서버 시작

```bash
python -m step5_api.server
# → http://localhost:8000
```

### 7. Next.js 프론트엔드 시작

```bash
cd frontend
cp .env.local.example .env.local
npm install
npm run dev
# → http://localhost:3000
```

### 8. 작업 제출 (API)

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"video_path": "C:\\path\\to\\your\\video.mp4"}'
```

응답 예시:
```json
{"job_id": "550e8400-e29b-41d4-a716-446655440000", "status": "pending"}
```

### 9. 처리 상태 확인

```bash
curl http://localhost:8000/jobs/{job_id}
```

### 10. 오버레이 메타데이터 조회

```bash
curl http://localhost:8000/overlay/{job_id}
```

---

## DB 테이블 구조

| 테이블 | 설명 |
|--------|------|
| `job_history` | 작업 이력 및 상태 관리 |
| `video_preprocessing_info` | 전처리 메타데이터 (경로, FPS, 해상도, scene_cut_times) |
| `analysis_vision_context` | 프레임별 비전 분석 결과 (safe area, 밀집도, 씬 설명) |
| `analysis_audio` | 묵음 구간 타임스탬프 |
| `analysis_transcript` | Whisper STT 세그먼트 (dialogue_segmenter 입력) |
| `analysis_scene` | 의미 단위 씬 분절 결과 (context_narrative 포함) |
| `ad_inventory` | 광고 소재 목록 (target_narrative, ad_category, ad_category_path) |
| `decision_result` | 최종 광고 삽입 결정 (좌표, 점수, 유사도, 씬 길이, 밀도) |

### ad_inventory 주요 컬럼

| 컬럼 | 설명 | 채워지는 시점 |
|------|------|--------------|
| `target_narrative` | Qwen2-VL 4차원 광고 서술문 | `analyze_ad_narrative.py` 실행 시 |
| `ad_category` | TVCF 카테고리 (예: `음료/기호식품`) | `tvcf_downloader.py` 다운로드 시 |
| `ad_category_path` | 카테고리 계층 배열 | `tvcf_downloader.py` 다운로드 시 |

---

## 스코어링 기준 (v2.10)

### 필터 (통과 실패 시 해당 광고 제외)

| 조건 | 처리 |
|------|------|
| `context_narrative` ↔ `target_narrative` 유사도 < 0.50 | Skip (맥락 무관 광고 제거) |
| `video_clip`: 씬 길이 < 광고 길이 | Skip (물리적 수용 불가) |

### 점수 산출

| 조건 | 점수 |
|------|------|
| narrative 유사도 스케일링 (0.40~1.0 → 0~+80) | 0 ~ +80 |
| 최적 윈도우 내 객체 밀집도 ≤ 0.3 | +20 |
| 최적 윈도우 내 침묵 구간 겹침 | +15 |
| `ad_category` ↔ `context_narrative` 유사도 ≥ 0.35 | +10 |
| 최적 윈도우 내 객체 밀집도 ≥ 0.7 | −40 |

### 최종 필터

| 조건 | 처리 |
|------|------|
| 총점 < 20 | 광고 없음 판정 (맥락 부적합) |

> `ad_category`가 NULL인 광고는 카테고리 보너스 없이 나머지 점수만으로 평가됩니다.
