# VOD Dynamic Ad Overlay System

비디오 문맥 분석 기반 동적 광고 오버레이 시스템

> **현재 버전: v2.15** | 음성 우선 분석 알고리즘 (Step2 A=오디오, B=비전)

---

## 버전 히스토리

| 버전 | 주요 변경 |
|------|-----------|
| v2.0 | 기본 키워드 스코어링 |
| v2.5 | target_narrative 1:1 매칭, Scene 분절 |
| v2.8 | YOLOv8l 교체, dialogue_segmenter |
| v2.10 | ad_category 카테고리 매칭 보너스 |
| v2.11 | Gemini VLM 백엔드 통합 |
| v2.12 | Step3→Step4 메시지 경량화 (candidates 제거, job_id만 전송) |
| v2.13 | Step2 3컨테이너 분리, Step4 쿼리 O(N)→O(1) 최적화 |
| v2.14 | Step4 씬 단위 광고 매칭 (cross-encoder pre-filter) |
| v2.15 | Step2 음성 우선 알고리즘 (A=오디오 STT/SBERT, B=비전 YOLO/Gemini, C 제거) |

---

## 아키텍처 개요

```
[광고 데이터 준비 (사전 작업)]
  populate_ad_inventory.py → ad_inventory (video_clip, banner)
  analyze_ad_narrative.py  → ad_inventory.target_narrative (Gemini 4차원 분석)

[파이프라인]
[FastAPI] ──POST /jobs──► [RabbitMQ: vod.prod.step1.preprocess]
                                │
                    ┌───────────▼────────────┐
                    │  Step-1 Preprocessing  │  ffmpeg 프레임/오디오 추출
                    │                        │  scenedetect 시각적 컷 감지
                    └───────────┬────────────┘
                                │ [vod.prod.step2a.audio]
                    ┌───────────▼────────────┐
                    │  Step-2A Audio         │  faster-whisper STT (large-v3)
                    │                        │  ko-sroberta SBERT 씬 분절
                    │                        │  침묵 감지 (librosa)
                    └───────────┬────────────┘
                                │ [vod.prod.step2b.vision]
                    ┌───────────▼────────────┐
                    │  Step-2B Vision        │  씬별 K프레임 선택
                    │                        │  YOLOv8l (safe area, 밀집도)
                    │                        │  Gemini (상황/감정/욕구 분석)
                    └───────────┬────────────┘
                                │ [vod.prod.step3.persist]
                    ┌───────────▼────────────┐
                    │  Step-3 Persistence    │  씬 × 광고 후보 페어 생성
                    └───────────┬────────────┘
                                │ [vod.prod.step4.decision]
                    ┌───────────▼────────────┐
                    │  Step-4 Decision       │  MiniLM pre-filter
                    │                        │  Cross-Encoder 스코어링
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
│   ├── common/                    # 공통 유틸 (DB, RabbitMQ, config)
│   │   ├── config.py              # 환경변수 설정
│   │   ├── db.py                  # PostgreSQL 연결
│   │   └── rabbitmq.py            # RabbitMQ 헬퍼
│   ├── step1_preprocessing/       # ffmpeg 추출, scenedetect
│   │   └── pipeline.py
│   ├── step2_analysis/            # 분석 파이프라인
│   │   ├── consumer_a.py          # Step2-A: 오디오 우선 (Whisper+SBERT)
│   │   ├── consumer_b.py          # Step2-B: 비전 후속 (YOLO+Gemini)
│   │   ├── audio_analysis.py      # librosa 묵음 구간 감지
│   │   ├── vision_yolo.py         # YOLOv8l safe area 분석
│   │   ├── vision_gemini.py       # Gemini 씬 서술 (상황/감정/욕구)
│   │   └── vision_qwen.py         # Qwen2-VL 씬 서술 (fallback)
│   ├── step3_persistence/         # 씬 × 광고 후보 페어 생성
│   │   └── pipeline.py
│   ├── step4_decision/            # 스코어링 → decision_result 저장
│   │   ├── decision.py            # 메인 스코어링 파이프라인
│   │   ├── pre_filter.py          # MiniLM pre-filter
│   │   ├── cross_encoder_scorer.py # Cross-Encoder 유사도
│   │   ├── embedding_scorer.py    # Sentence-Transformers 임베딩
│   │   └── ad_narrative_gemini.py # Gemini 광고 narrative 생성
│   ├── step5_api/                 # FastAPI REST 서버
│   │   └── server.py
│   ├── init_schema.sql            # DB DDL (전체 스키마 + 마이그레이션)
│   ├── init_db.py                 # DB 초기화
│   ├── populate_ad_inventory.py   # 로컬 광고 파일 등록
│   └── analyze_ad_narrative.py    # Gemini 광고 narrative 생성
├── frontend/                      # Next.js 14 App
│   └── src/
│       ├── app/
│       │   ├── page.tsx           # 홈 (작업 제출 / 플레이어 접근)
│       │   └── player/[jobId]/    # VOD 플레이어 페이지
│       └── components/
│           ├── VideoPlayer.tsx    # HTML5 비디오 + 오버레이 렌더링
│           └── AdOverlay.tsx      # 단일 광고 오버레이 (영상/이미지)
├── docker-compose.pipeline.yml    # 서버 Docker 파이프라인 (v2.15)
├── COMMANDS.md                    # 서버 배포/운영 명령어
├── PIPELINE_v2.md                 # 파이프라인 전체 문서
├── start.ps1 / start.bat          # 로컬 서비스 시작
├── stop.ps1  / stop.bat           # 로컬 서비스 중지
└── status.ps1 / status.bat        # 로컬 서비스 상태 확인
```

---

## 로컬 실행 가이드

### 1. 사전 준비

```bash
# Python 가상환경 생성 (프로젝트 루트)
python -m venv .venv
.venv\Scripts\activate

# 단계별 패키지 설치
pip install -r backend/requirements.step1.txt
pip install -r backend/requirements.step2a.txt   # faster-whisper, sentence-transformers
pip install -r backend/requirements.step2b.txt   # ultralytics, google-genai
pip install -r backend/requirements.step3.txt
pip install -r backend/requirements.step4.txt
pip install -r backend/requirements.step5.txt
```

### 2. DB 스키마 초기화

```bash
cd backend
python init_db.py
```

### 3. 광고 인벤토리 등록

```bash
python populate_ad_inventory.py
python analyze_ad_narrative.py        # Gemini 광고 narrative 생성
python analyze_ad_narrative.py --force  # 전체 재분석
```

### 4. 서비스 시작 (Windows — 권장)

```powershell
# 전체 시작 (Step1~5 + Frontend)
.\start.ps1

# Frontend 제외
.\start.ps1 -SkipFrontend

# 상태 확인
.\status.ps1

# 전체 중지
.\stop.ps1
```

### 5. 수동 시작 (터미널 7개)

```bash
# 터미널 1 — Step1 전처리
cd backend && python -m step1_preprocessing.pipeline --consume

# 터미널 2 — Step2-A 오디오 (Whisper + SBERT)
cd backend && python -m step2_analysis.consumer_a

# 터미널 3 — Step2-B 비전 (YOLO + Gemini)
cd backend && python -m step2_analysis.consumer_b

# 터미널 4 — Step3 영속성
cd backend && python -m step3_persistence.pipeline

# 터미널 5 — Step4 의사결정
cd backend && python -m step4_decision.decision

# 터미널 6 — Step5 API
cd backend && python -m step5_api.server
# → http://localhost:8000/docs

# 터미널 7 — Frontend
cd frontend && npm run dev
# → http://localhost:3000
```

### 6. 작업 제출

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"video_path": "C:\\path\\to\\video.mp4"}'
```

```json
{"job_id": "550e8400-...", "status": "pending"}
```

---

## DB 테이블 구조

| 테이블 | 설명 |
|--------|------|
| `job_history` | 작업 이력 및 상태 관리 |
| `video_preprocessing_info` | 전처리 메타데이터 (FPS, 해상도, scene_cut_times) |
| `analysis_audio` | librosa 묵음 구간 타임스탬프 |
| `analysis_transcript` | faster-whisper STT 세그먼트 |
| `analysis_scene` | SBERT 씬 분절 결과 (situation, emotion, desire 포함) |
| `analysis_vision_context` | 프레임별 YOLO 분석 결과 (safe area, 밀집도) |
| `ad_inventory` | 광고 소재 목록 (target_narrative, ad_category) |
| `decision_result` | 최종 광고 삽입 결정 (좌표, 점수, 씬 길이, 밀도) |

### analysis_scene 주요 컬럼 (v2.15 신규)

| 컬럼 | 설명 |
|------|------|
| `situation` | 씬 상황 (Gemini 분석) |
| `emotion` | 씬 감정 (Gemini 분석) |
| `desire` | 씬 욕구/니즈 (Gemini 분석) |

---

## 스코어링 기준 (v2.14)

### Step4 처리 흐름

```
후보 페어 (씬 × 광고)
    │
    ▼ MiniLM pre-filter (임베딩 유사도 ≥ 0.35)
    │
    ▼ Cross-Encoder 정밀 스코어링
    │
    ▼ 슬라이딩 윈도우 최적 타임스탬프
    │
    ▼ decision_result 저장
```

### 점수 산출

| 조건 | 점수 |
|------|------|
| narrative 유사도 스케일링 (0.40~1.0 → 0~+80) | 0 ~ +80 |
| 최적 윈도우 내 객체 밀집도 ≤ 0.3 | +20 |
| 최적 윈도우 내 침묵 구간 겹침 | +15 |
| ad_category ↔ context_narrative 유사도 ≥ 0.35 | +10 |
| 최적 윈도우 내 객체 밀집도 ≥ 0.7 | −40 |

---

## 환경 변수 (.env)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `DB_HOST` | `121.167.223.17` | PostgreSQL 호스트 |
| `DB_PORT` | `5432` | PostgreSQL 포트 |
| `DB_NAME` | `hv02` | DB 이름 |
| `DB_USER` | `postgres01` | DB 사용자 |
| `DB_PASSWORD` | `postgres01` | DB 비밀번호 |
| `RABBITMQ_HOST` | `121.167.223.17` | RabbitMQ 호스트 |
| `GEMINI_API_KEY` | — | Gemini API 키 (필수) |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini 모델 |
| `VLM_BACKEND` | `gemini` | VLM 백엔드 (`gemini` / `qwen`) |
| `FASTER_WHISPER_MODEL` | `large-v3` | Whisper 모델 크기 |
| `SBERT_MODEL` | `snunlp/KR-SBERT-V40K-klueNLI-augSTS` | SBERT 모델 |

---

## 서버 배포

GitHub main 브랜치에 push/merge 시 **자동 배포**됩니다.

```
GitHub main push
    → GitHub Actions deploy.yml
    → SCP 파일 전송 (121.167.223.17)
    → docker-compose down → build → up -d
```

수동 배포는 `COMMANDS.md` 참조.
