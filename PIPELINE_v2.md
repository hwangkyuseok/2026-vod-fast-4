# VOD Ad Overlay System — 파이프라인 전체 문서 v2

> **2026_VOD_FAST_4** | 비디오 문맥 분석 기반 동적 광고 오버레이 시스템
> 현재 버전: **v2.6 (Scene-driven) + Docker 배포**

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [인프라 연결 정보](#2-인프라-연결-정보)
3. [Docker 배포 구조](#3-docker-배포-구조)
4. [서버 전송 → 빌드 → 실행 명령어](#4-서버-전송--빌드--실행-명령어)
5. [데이터베이스 스키마](#5-데이터베이스-스키마)
6. [RabbitMQ 큐 구조](#6-rabbitmq-큐-구조)
7. [파이프라인 단계별 상세](#7-파이프라인-단계별-상세)
8. [공통 모듈](#8-공통-모듈)
9. [프론트엔드 (Next.js)](#9-프론트엔드-nextjs)
10. [Step 5 REST API](#10-step-5-rest-api)
11. [스코어링 로직](#11-스코어링-로직)
12. [광고 분석 서비스 (analyze-narrative)](#12-광고-분석-서비스-analyze-narrative)
13. [유틸리티 스크립트](#13-유틸리티-스크립트)
14. [함수 전체 목록](#14-함수-전체-목록)

---

## 1. 시스템 개요

4단계 AI 분석 파이프라인이 RabbitMQ를 통해 비동기로 연결되며, PostgreSQL에 데이터를 축적한 뒤 Next.js 프론트엔드에서 광고를 실시간 오버레이한다. 전체 서비스는 Docker Compose로 리눅스 서버에서 구동된다.

### 전체 파이프라인 흐름

```
[사전 준비] analyze_ad_narrative (Docker) → ad_inventory.target_narrative 채움
        |
[사용자: 브라우저에서 영상 선택 → 분석 시작]
        |
  Step 1: Preprocessing    ffmpeg로 프레임/오디오 추출
        |
  Step 2: Analysis         YOLOv8l + Qwen2-VL + Whisper + librosa
                           Phase A: 씬 세그멘테이션 → analysis_scene (context_narrative)
                           Phase B: 침묵 감지 + YOLO safe area
                           Phase C: 침묵 구간 → 해당 씬의 context_narrative 복사
        |
  Step 3: Candidates       analysis_scene × ad_inventory Cartesian product
                           context_narrative, target_narrative 포함
        |
  Step 4: Scoring          [1차] similarity ≥ 0.30 필터
                           [2차] video_clip: scene_duration ≥ ad_duration
                           [3차] 1초 슬라이딩 윈도우 → 최적 삽입 타임스탬프
                           [가점] 침묵 구간 겹침 +15
                           → 씬당 최고점 광고 1개 선택
        |
  Step 5: API (FastAPI)    오버레이 메타데이터 제공 + 미디어 스트리밍
        |
  [브라우저: VOD + 광고 오버레이 실시간 재생]
```

### 기술 스택

| 영역 | 기술 |
|------|------|
| 언어/프레임워크 | Python 3.11, FastAPI, Next.js 14 |
| 메시지 브로커 | RabbitMQ (pika) |
| 데이터베이스 | PostgreSQL (psycopg2) |
| 객체 감지 | YOLOv8l (ultralytics 8.4.21) |
| 장면 이해 | Qwen2-VL-2B-Instruct |
| 의미 임베딩 | sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2) |
| STT | OpenAI Whisper small (Docker) / base (로컬) |
| 음성 분석 | librosa |
| 미디어 처리 | ffmpeg, ffprobe |
| 컨테이너 | Docker Compose v1 (하이픈 버전) |

---

## 2. 인프라 연결 정보

| 항목 | 값 |
|------|----|
| PostgreSQL | `121.167.223.17:5432` DB=`hv02` user=`postgres01` pw=`postgres01` |
| RabbitMQ | `121.167.223.17:5672` user=`admin` pw=`admin` |
| 광고 영상 (호스트) | `/app/HelloVision/data/ad_assets/video/` |
| 광고 이미지 (호스트) | `/app/HelloVision/data/ad_assets/image/` |
| VOD 영상 (호스트) | `/app/HelloVision/data/vod/` |
| 스토리지 (호스트) | `/app/HelloVision/data/storage/` |
| API 외부 접근 URL | `http://121.167.223.17:8000` |
| 프론트엔드 URL | `http://121.167.223.17:3000` |

### 서버 배포 경로

| 역할 | 경로 |
|------|------|
| 파이프라인 Dockerfile / 소스 | `/app/Docker/pipeline/` |
| 프론트엔드 Dockerfile / 소스 | `/app/Docker/frontend/` |
| docker-compose.pipeline.yml | `/app/Docker/pipeline/docker-compose.pipeline.yml` |
| analyze-narrative Docker | `/app/Docker/analyze-narrative/` |

---

## 3. Docker 배포 구조

### 이미지 3개 / 서비스 6개

| 이미지 | 빌드 대상 | 사용 서비스 | 특징 |
|--------|----------|-----------|------|
| `vod-backend:latest` | `Dockerfile.backend` | step1, step3, step4, step5-api | CPU-only torch, 경량 |
| `vod-step2:latest` | `Dockerfile.step2` | step2 | YOLO + Whisper + Qwen, GPU 선택적 |
| `vod-frontend:latest` | `Dockerfile.frontend` | frontend | Next.js 멀티스테이지 빌드 |

### 서비스 구성 (`docker-compose.pipeline.yml`)

| 서비스 | 이미지 | 포트 | 역할 |
|--------|--------|------|------|
| `step1` | vod-backend | - | 프레임/오디오 추출, RabbitMQ 소비 |
| `step2` | vod-step2 | - | YOLOv8l + Qwen2-VL + Whisper, RabbitMQ 소비 |
| `step3` | vod-backend | - | 후보 생성, RabbitMQ 소비 |
| `step4` | vod-backend | - | 스코어링/결정, RabbitMQ 소비 |
| `step5-api` | vod-backend | **8000** | FastAPI REST + 미디어 서빙 |
| `frontend` | vod-frontend | **3000** | Next.js 프론트엔드 |

### 볼륨 마운트

| 호스트 경로 | 컨테이너 경로 | 서비스 |
|------------|-------------|--------|
| `/app/HelloVision/data/vod` | `/vod:ro` | step1, step5-api |
| `/app/HelloVision/data/storage` | `/app/storage` | step1~5 공통 |
| `/app/HelloVision/data/logs` | `/app/storage/logs` | step1~5 공통 |
| `/app/HelloVision/data/ad_assets/video` | `/ads/video:ro` | step5-api |
| `/app/HelloVision/data/ad_assets/image` | `/ads/banner:ro` | step5-api |
| `qwen_model_cache` (named) | `/models` | step2 |
| `sentence_model_cache` (named) | `/models` | step4 |

### 주요 환경변수 (step5-api)

| 변수 | 값 |
|------|----|
| `API_BASE_URL` | `http://121.167.223.17:8000` |
| `VOD_DIR` | `/vod` |
| `AD_VIDEO_DIR` | `/ads/video` |
| `AD_IMAGE_DIR` | `/ads/banner` |

---

## 4. 서버 전송 → 빌드 → 실행 명령어

> 서버 Docker Compose는 **v1** (하이픈 버전): `docker-compose` 사용

### 4.1 소스 파일 서버 전송 (SCP)

#### 백엔드 전체 전송
```bash
# 공통 모듈
scp backend/common/config.py     vhcalnplci@121.167.223.17:/app/Docker/pipeline/common/config.py
scp backend/common/db.py         vhcalnplci@121.167.223.17:/app/Docker/pipeline/common/db.py
scp backend/common/rabbitmq.py   vhcalnplci@121.167.223.17:/app/Docker/pipeline/common/rabbitmq.py

# Step 1
scp backend/step1_preprocessing/pipeline.py \
    vhcalnplci@121.167.223.17:/app/Docker/pipeline/step1_preprocessing/pipeline.py

# Step 2
scp backend/step2_analysis/consumer.py         vhcalnplci@121.167.223.17:/app/Docker/pipeline/step2_analysis/consumer.py
scp backend/step2_analysis/vision_yolo.py      vhcalnplci@121.167.223.17:/app/Docker/pipeline/step2_analysis/vision_yolo.py
scp backend/step2_analysis/vision_qwen.py      vhcalnplci@121.167.223.17:/app/Docker/pipeline/step2_analysis/vision_qwen.py
scp backend/step2_analysis/audio_analysis.py   vhcalnplci@121.167.223.17:/app/Docker/pipeline/step2_analysis/audio_analysis.py
scp backend/step2_analysis/audio_transcription.py vhcalnplci@121.167.223.17:/app/Docker/pipeline/step2_analysis/audio_transcription.py
scp backend/step2_analysis/dialogue_segmenter.py  vhcalnplci@121.167.223.17:/app/Docker/pipeline/step2_analysis/dialogue_segmenter.py

# Step 3
scp backend/step3_persistence/pipeline.py \
    vhcalnplci@121.167.223.17:/app/Docker/pipeline/step3_persistence/pipeline.py

# Step 4
scp backend/step4_decision/scoring.py         vhcalnplci@121.167.223.17:/app/Docker/pipeline/step4_decision/scoring.py
scp backend/step4_decision/embedding_scorer.py vhcalnplci@121.167.223.17:/app/Docker/pipeline/step4_decision/embedding_scorer.py

# Step 5 API
scp backend/step5_api/server.py vhcalnplci@121.167.223.17:/app/Docker/pipeline/step5_api/server.py
```

#### 프론트엔드 전체 전송
```bash
scp frontend/src/app/page.tsx \
    vhcalnplci@121.167.223.17:/app/Docker/frontend/src/app/page.tsx

scp "frontend/src/app/player/[jobId]/page.tsx" \
    "vhcalnplci@121.167.223.17:/app/Docker/frontend/src/app/player/[jobId]/page.tsx"

scp frontend/src/components/VideoPlayer.tsx \
    vhcalnplci@121.167.223.17:/app/Docker/frontend/src/components/VideoPlayer.tsx

scp frontend/src/components/AdOverlay.tsx \
    vhcalnplci@121.167.223.17:/app/Docker/frontend/src/components/AdOverlay.tsx

scp frontend/src/types/overlay.ts \
    vhcalnplci@121.167.223.17:/app/Docker/frontend/src/types/overlay.ts

scp frontend/next.config.js \
    vhcalnplci@121.167.223.17:/app/Docker/frontend/next.config.js
```

> **PowerShell 주의**: `[jobId]` 경로는 반드시 큰따옴표(`"`)로 감싸야 함

### 4.2 Docker 이미지 빌드

```bash
ssh vhcalnplci@121.167.223.17

cd /app/Docker/pipeline

# vod-backend:latest 빌드 (step1/3/4/5 공용)
# → step2_analysis 제외 나머지 소스가 바뀌었을 때
docker-compose -f docker-compose.pipeline.yml build step1

# vod-step2:latest 빌드 (step2 전용 heavy ML)
# → step2_analysis 소스가 바뀌었을 때
docker-compose -f docker-compose.pipeline.yml build step2

# vod-frontend:latest 빌드 (Next.js)
# → 프론트엔드 소스가 바뀌었을 때
docker-compose -f docker-compose.pipeline.yml build frontend

# 전체 동시 빌드
docker-compose -f docker-compose.pipeline.yml build
```

### 4.3 서비스 실행 / 재시작

```bash
# ── 전체 서비스 기동 (최초 실행) ────────────────────────────────────────────
docker-compose -f docker-compose.pipeline.yml up -d

# ── 특정 서비스만 재시작 (코드 변경 없이 설정만 바뀐 경우) ─────────────────
docker-compose -f docker-compose.pipeline.yml restart step5-api
docker-compose -f docker-compose.pipeline.yml restart frontend

# ── 특정 서비스 이미지 교체 후 재생성 (빌드 후 반드시 up -d 필요) ───────────
# vod-backend 이미지 교체 → step1/3/4/step5-api 동시 재생성
docker-compose -f docker-compose.pipeline.yml up -d step5-api

# frontend 이미지 교체 → frontend 컨테이너 재생성
docker-compose -f docker-compose.pipeline.yml up -d frontend

# step2 이미지 교체
docker-compose -f docker-compose.pipeline.yml up -d step2

# ── 전체 중지 ────────────────────────────────────────────────────────────────
docker-compose -f docker-compose.pipeline.yml down

# ── 상태 확인 ────────────────────────────────────────────────────────────────
docker-compose -f docker-compose.pipeline.yml ps

# ── 로그 확인 ────────────────────────────────────────────────────────────────
docker-compose -f docker-compose.pipeline.yml logs -f step2
docker-compose -f docker-compose.pipeline.yml logs -f step5-api
docker-compose -f docker-compose.pipeline.yml logs -f frontend
```

### 4.4 파일 1개 수정 시 최소 명령어 (예: server.py 수정)

```bash
# 1) 로컬에서 서버로 전송
scp backend/step5_api/server.py vhcalnplci@121.167.223.17:/app/Docker/pipeline/step5_api/server.py

# 2) vod-backend 이미지 재빌드
ssh vhcalnplci@121.167.223.17 \
  "docker-compose -f /app/Docker/pipeline/docker-compose.pipeline.yml build step1"

# 3) step5-api 컨테이너 재생성
ssh vhcalnplci@121.167.223.17 \
  "docker-compose -f /app/Docker/pipeline/docker-compose.pipeline.yml up -d step5-api"
```

### 4.5 신규 서버 세팅 (최초 1회)

```bash
# ① 서버 디렉토리 생성
ssh vhcalnplci@121.167.223.17 "
  mkdir -p /app/Docker/pipeline/{common,step1_preprocessing,step2_analysis,step3_persistence,step4_decision,step5_api}
  mkdir -p /app/Docker/frontend/src/{app,components,types}
  mkdir -p '/app/Docker/frontend/src/app/player/[jobId]'
  mkdir -p /app/Docker/frontend/src/app/api
  mkdir -p /app/HelloVision/data/{vod,storage,logs,ad_assets/video,ad_assets/image}
"

# ② 모든 파일 전송 (4.1 명령어 실행)

# ③ docker-compose.pipeline.yml 전송
scp docker-compose.pipeline.yml \
    vhcalnplci@121.167.223.17:/app/Docker/pipeline/docker-compose.pipeline.yml

# ④ Dockerfile 전송
scp backend/Dockerfile.backend  vhcalnplci@121.167.223.17:/app/Docker/pipeline/Dockerfile.backend
scp backend/Dockerfile.step2    vhcalnplci@121.167.223.17:/app/Docker/pipeline/Dockerfile.step2
scp frontend/Dockerfile.frontend vhcalnplci@121.167.223.17:/app/Docker/frontend/Dockerfile.frontend

# ⑤ DB 초기화
ssh vhcalnplci@121.167.223.17 \
  "docker-compose -f /app/Docker/pipeline/docker-compose.pipeline.yml run --rm step5-api python init_db.py"

ssh vhcalnplci@121.167.223.17 \
  "docker-compose -f /app/Docker/pipeline/docker-compose.pipeline.yml run --rm step5-api python populate_ad_inventory.py"

# ⑥ 전체 빌드 & 기동
ssh vhcalnplci@121.167.223.17 "
  docker-compose -f /app/Docker/pipeline/docker-compose.pipeline.yml build
  docker-compose -f /app/Docker/pipeline/docker-compose.pipeline.yml up -d
"
```

---

## 5. 데이터베이스 스키마

### 테이블 목록

| 테이블 | 역할 |
|--------|------|
| `job_history` | 작업 메타데이터 및 상태 추적 |
| `video_preprocessing_info` | Step 1 추출 결과 |
| `analysis_vision_context` | YOLOv8l + Qwen2-VL 프레임 분석 |
| `analysis_audio` | 음성 침묵 구간 + 씬 context |
| `analysis_transcript` | Whisper STT 자막 |
| `analysis_scene` | 씬 세그멘테이션 결과 + context_narrative |
| `ad_inventory` | 광고 자산 카탈로그 |
| `decision_result` | 최종 광고 삽입 결정 |
| `ad_placement_feedback` | 광고 배치 적합성 피드백 (레이블 수집) |

### 스키마 상세

#### `job_history`
```sql
job_id           UUID PRIMARY KEY
status           TEXT   -- pending | preprocessing | analysing | persisting | deciding | complete | failed
input_video_path TEXT
error_message    TEXT
created_at       TIMESTAMP
updated_at       TIMESTAMP
```

#### `video_preprocessing_info`
```sql
id                  SERIAL PRIMARY KEY
job_id              UUID REFERENCES job_history
original_video_path TEXT
audio_path          TEXT   -- storage/jobs/{job_id}/audio.wav
frame_dir_path      TEXT   -- storage/jobs/{job_id}/frames/
duration_sec        FLOAT
fps                 FLOAT
width               INT
height              INT
total_frames        INT
created_at          TIMESTAMP
```

#### `analysis_vision_context`
```sql
id               SERIAL PRIMARY KEY
job_id           UUID REFERENCES job_history
frame_index      INT
timestamp_sec    FLOAT
safe_area_x      INT    -- 광고 삽입 가능 영역 (YOLOv8l 기반)
safe_area_y      INT
safe_area_w      INT
safe_area_h      INT
object_density   FLOAT  -- 0.0~1.0
scene_description TEXT  -- Qwen2-VL 장면 설명 (영어)
is_scene_cut     BOOLEAN
created_at       TIMESTAMP

UNIQUE(job_id, frame_index)
```

#### `analysis_audio`
```sql
id                SERIAL PRIMARY KEY
job_id            UUID REFERENCES job_history
silence_start_sec FLOAT
silence_end_sec   FLOAT
duration_sec      FLOAT GENERATED ALWAYS AS (silence_end_sec - silence_start_sec)
context_tags      TEXT[]  -- 레거시 키워드 배열
context_summary   TEXT    -- 해당 씬의 context_narrative가 복사됨 (가점용)
created_at        TIMESTAMP

UNIQUE(job_id, silence_start_sec, silence_end_sec)
```

#### `analysis_transcript`
```sql
id         SERIAL PRIMARY KEY
job_id     UUID REFERENCES job_history
start_sec  FLOAT
end_sec    FLOAT
text       TEXT   -- Whisper 변환 텍스트 (영어, task='translate')
created_at TIMESTAMP

UNIQUE(job_id, start_sec, end_sec)
```

#### `analysis_scene`
```sql
id                SERIAL PRIMARY KEY
job_id            UUID REFERENCES job_history ON DELETE CASCADE
scene_start_sec   FLOAT NOT NULL
scene_end_sec     FLOAT NOT NULL
context_narrative TEXT   -- Qwen2-VL 4차원 씬 서술문
created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()

UNIQUE(job_id, scene_start_sec)
```

#### `ad_inventory`
```sql
ad_id            TEXT PRIMARY KEY
ad_name          TEXT
ad_type          TEXT    -- video_clip | banner
resource_path    TEXT
duration_sec     FLOAT
target_mood      TEXT[]  -- 레거시 (fallback용)
target_narrative TEXT    -- 4차원 광고 서술문 (analyze_ad_narrative.py로 생성)
width            INT
height           INT
```

#### `decision_result`
```sql
id                     SERIAL PRIMARY KEY
job_id                 UUID REFERENCES job_history
ad_id                  TEXT REFERENCES ad_inventory
overlay_start_time_sec FLOAT
overlay_duration_sec   FLOAT
coordinates_x          INT
coordinates_y          INT
coordinates_w          INT
coordinates_h          INT
score                  INT
created_at             TIMESTAMP
```

#### `ad_placement_feedback`
```sql
id           SERIAL PRIMARY KEY
decision_id  INT REFERENCES decision_result(id)
label        INT    -- -1=부적합, 0=보통, 1=적합
source       TEXT   -- 'user' | 'auto'
created_at   TIMESTAMP DEFAULT NOW()

UNIQUE(decision_id)  -- 중복 제출 시 UPSERT
```

---

## 6. RabbitMQ 큐 구조

```
QUEUE_STEP1: vod.step1.preprocess
       ↓
QUEUE_STEP2: vod.step2.analysis
       ↓
QUEUE_STEP3: vod.step3.persistence
       ↓
QUEUE_STEP4: vod.step4.decision
```

| 큐 | 발행자 | 구독자 | 페이로드 |
|----|--------|--------|---------|
| `vod.step1.preprocess` | Step5 API | Step 1 | `{"job_id": str, "video_path": str}` |
| `vod.step2.analysis` | Step 1 | Step 2 | `{"job_id": str}` |
| `vod.step3.persistence` | Step 2 | Step 3 | `{"job_id": str}` |
| `vod.step4.decision` | Step 3 | Step 4 | `{"job_id": str, "candidates": list[dict]}` |

---

## 7. 파이프라인 단계별 상세

### 7.1 Step 1 — Preprocessing

**파일**: `step1_preprocessing/pipeline.py`
**이미지**: `vod-backend:latest`

| 함수 | 역할 |
|------|------|
| `extract_audio(video_path, output_dir)` | ffmpeg → WAV (16kHz mono PCM) |
| `extract_frames(video_path, output_dir, fps)` | ffmpeg → JPEG 1fps |
| `get_video_metadata(video_path)` | ffprobe → duration/fps/해상도 |
| `save_to_db(job_id, paths, meta)` | `video_preprocessing_info` INSERT |
| `run(job_id, video_path)` | Step 1 전체 실행 |

**생성 파일**
```
storage/jobs/{job_id}/
  audio.wav               16kHz mono PCM
  frames/
    frame_000001.jpg      1fps JPEG
    ...
```

---

### 7.2 Step 2 — Multimodal Analysis

**파일**: `step2_analysis/` (6개 파일)
**이미지**: `vod-step2:latest`

#### `vision_yolo.py` — YOLOv8l 객체 감지

| 함수 | 역할 |
|------|------|
| `_get_model()` | YOLOv8l 싱글톤 로드 |
| `_compute_safe_area(frame_shape, boxes, person_boxes)` | safe_area + object_density + exclusion zone |
| `_is_scene_cut(prev_gray, curr_gray)` | 장면 전환 감지 |
| `analyse_frames(frame_paths, on_batch)` | 배치 스트리밍 (200프레임 단위) |

**exclusion zone 3개**
- 프레임 상단 12%: 제목/로고 영역
- 프레임 하단 8%: 자막 영역
- COCO class 0 (person) bbox 상단 50% + 3% 패딩: 얼굴 영역

#### `vision_qwen.py` — Qwen2-VL 분석

| 함수 | 역할 |
|------|------|
| `analyse_scene_context(frame_paths, transcript_text, scene_start_sec, scene_end_sec)` | 씬 전체 → 1-2문장 context_narrative (다중 프레임) |
| `_clean_vlm_response(text)` | VLM 출력 정규화 (마크다운/개행 → 단일 문자열) |
| `_describe_frame(frame_path)` | 단일 프레임 → 장면 설명 |
| `analyse_frames(frame_paths)` | 샘플 프레임 분석 |
| `analyse_context_narrative(...)` | 서술문 생성 (레거시) |

#### `audio_analysis.py` — 침묵 감지

librosa 기반, RMS -40dB 이하 + 최소 1초 구간 추출

#### `audio_transcription.py` — Whisper STT

Whisper small (Docker), `task='translate'` → 영어 변환 → `analysis_transcript` 저장

#### `dialogue_segmenter.py` — 씬 세그멘테이션

| 함수 | 역할 |
|------|------|
| `segment_video(transcript_segments, total_duration_sec, min_scene_sec)` | 전방향 씬 경계 탐지 → 씬 목록 |
| `find_context_start(transcript_segments, silence_start_sec, ...)` | 가변 컨텍스트 윈도우 시작 시각 |

**`segment_video()` 알고리즘**
```
1. transcript를 15초 청크로 묶기
2. 모든 청크 임베딩 (sentence-transformers)
3. 인접 청크 간 cosine sim < 0.52 → 씬 경계
4. min_scene_sec(30s)보다 짧은 씬 → 이전 씬에 병합
5. 마지막 씬은 total_duration_sec에서 종료
Fallback: transcript 없음 → 단일 씬 [0, total_duration_sec]
```

#### `consumer.py` — Step 2 오케스트레이터 (Phase A/B/C)

```
Phase B (독립):
  1. YOLOv8l → analysis_vision_context INSERT (200프레임 배치)
  2. Qwen2-VL → analysis_vision_context UPDATE (scene_description)
  3. librosa → analysis_audio INSERT (침묵 구간)
  4. Whisper → analysis_transcript INSERT

Phase A (전방향 씬 세그멘테이션):
  5. segment_video() → 씬 경계 목록
  6. _sample_frames_for_scene() → 씬별 균등 4프레임 (linspace)
  7. analyse_scene_context() → context_narrative
  8. _insert_scene_context() → analysis_scene INSERT

Phase C (침묵 → 씬 매핑):
  9. _assign_scene_context_to_silences()
     → analysis_audio.context_summary UPDATE (씬 context_narrative 복사)

→ QUEUE_STEP3 발행
```

---

### 7.3 Step 3 — Candidate Building

**파일**: `step3_persistence/pipeline.py`
**이미지**: `vod-backend:latest`

`analysis_scene` × `ad_inventory` Cartesian product → QUEUE_STEP4

| 함수 | 역할 |
|------|------|
| `_get_scene_intervals(job_id)` | `analysis_scene` 조회 (씬 목록 + context_narrative) |
| `_get_ad_inventory()` | 광고 목록 조회 (`target_narrative` 포함) |
| `build_candidates(job_id)` | 씬 × 광고 후보 쌍 생성 |
| `run` | Step 3 실행 |

**후보 dict 구조**
```python
{
    "scene_start_sec":   float,
    "scene_end_sec":     float,
    "scene_duration":    float,
    "context_narrative": str,      # analysis_scene에서 직접 읽음
    "ad_id":             str,
    "ad_name":           str,
    "ad_type":           str,
    "ad_duration_sec":   float | None,
    "target_narrative":  str,      # 4차원 광고 서술문
    "target_mood":       list[str], # fallback용
}
```

---

### 7.4 Step 4 — Scoring & Decision

**파일**: `step4_decision/scoring.py` + `embedding_scorer.py`
**이미지**: `vod-backend:latest`

#### `embedding_scorer.py`

**모델**: `paraphrase-multilingual-MiniLM-L12-v2` (한/영 동시 지원, 384차원)

| 함수 | 역할 |
|------|------|
| `score_narrative_fit(context_narrative, ad_narrative)` | 주 경로 — narrative 1:1 코사인 유사도 |
| `score_ad_context_fit(context_summary, ad_name, target_mood)` | 레거시 fallback |
| `compute_similarity(text_a, text_b)` | 코사인 유사도 |
| `embed(text)` | 정규화 임베딩 벡터 |
| `is_available()` | 모델 로드 여부 |

#### 스코어링 흐름

```
FOR EACH candidate (scene × ad):

  [1차 필터 — Context Matching]
  similarity = score_narrative_fit(context_narrative, target_narrative)
               (target_narrative 없으면 score_ad_context_fit fallback)
  similarity < 0.30 → Skip (슬라이딩 윈도우 연산 생략)

  [2차 필터 — 물리적 수용성]
  video_clip AND scene_duration < ad_duration → Skip
  banner → 항상 통과

  [3차 — 슬라이딩 윈도우]
  _find_best_overlay_window(job_id, scene_start, scene_end, window_duration)
    → 씬 내 1초 단위: max(safe_area 교집합 px) → min(avg_density)

  [점수 산출]
  +0~80  similarity ≥ 0.25 → scaled
  +20    window avg_density ≤ 0.3
  +15    _get_silence_overlap() — 침묵 가점
  -40    window avg_density ≥ 0.7

→ _pick_best_and_deduplicate()  ← scene_start_sec 기준, greedy 겹침 제거
→ _insert_decision_results()    ← overlay_start_time_sec = window 최적 시점
```

#### Step 4 함수 목록

| 함수 | 역할 |
|------|------|
| `_get_scene_frames(job_id, scene_start, scene_end)` | 씬 범위 vision 프레임 전체 로드 |
| `_intersect_safe_areas(frames)` | 다중 프레임 safe_area 교집합 직사각형 |
| `_find_best_overlay_window(job_id, scene_start, scene_end, duration)` | 1초 슬라이딩 윈도우 |
| `_get_silence_overlap(job_id, window_start, window_end)` | 침묵 가점 여부 |
| `_compute_score(candidate, job_id)` | 1차→2차→3차 필터 + 점수 → `(score, window)` |
| `_pick_best_and_deduplicate` | 씬당 최고점 + greedy 겹침 제거 |
| `_insert_decision_results` | DELETE-before-INSERT |
| `run` | Step 4 실행 |

---

## 8. 공통 모듈

### `common/config.py` 주요 설정

| 변수 | 기본값 | 용도 |
|------|--------|------|
| `YOLO_MODEL` | `yolov8l.pt` | 모델 크기 |
| `YOLO_CONFIDENCE_THRESHOLD` | `0.35` | bbox 신뢰도 필터 |
| `YOLO_BATCH_SIZE` | `200` | DB 플러시 단위 |
| `WHISPER_MODEL` | 환경변수 `WHISPER_MODEL` (Docker: `small`) | Whisper 크기 |
| `SILENCE_THRESHOLD_DB` | `-40.0` | 침묵 기준 |
| `MIN_SILENCE_DURATION_SEC` | `1.0` | 최소 침묵 길이 |
| `STORAGE_BASE` | `/app/storage` | 스토리지 루트 |
| `AD_VIDEO_DIR` | 환경변수 | 광고 영상 디렉토리 |
| `AD_IMAGE_DIR` | 환경변수 | 광고 이미지 디렉토리 |
| `API_BASE_URL` | 환경변수 | 외부 접근 URL (미디어 URL 생성용) |
| `VOD_DIR` | 환경변수 `/vod` | 원본 영상 디렉토리 |

---

## 9. 프론트엔드 (Next.js)

### 파일 구조

```
frontend/src/
  app/
    layout.tsx                 루트 레이아웃
    page.tsx                   홈 (작업 제출 + 완료 작업 재생)
    player/[jobId]/page.tsx    플레이어 페이지 (폴링)
    api/                       (레거시, 현재 미사용)
  components/
    VideoPlayer.tsx            메인 플레이어 + 오버레이 목록
    AdOverlay.tsx              단일 광고 오버레이
  types/
    overlay.ts                 타입 정의
  next.config.js               API 프록시 설정
```

### API 프록시 (`next.config.js`)

Next.js rewrite를 통해 브라우저 요청을 백엔드로 프록시:

```js
const backendUrl = process.env.BACKEND_URL || "http://localhost:8000";
// /api/backend/* → http://step5-api:8000/*  (Docker 내부)
// /api/backend/* → http://localhost:8000/*  (로컬 개발)
```

### `app/page.tsx` — 홈 페이지

**섹션 1: 영상 분석 작업 제출**
- `/api/backend/vod/files` 호출 → `/vod` 디렉토리 영상 파일 드롭다운
- 영상 선택 후 "분석 시작" → `POST /api/backend/jobs`

**섹션 2: 분석 완료된 Job 재생**
- `/api/backend/jobs/completed` 호출 → 완료된 작업 드롭다운
- 항목: `파일명 · 날짜` (최신순)
- "↻ 새로고침" 버튼 (수동 갱신)
- 새 분석 제출 후 자동 갱신
- 선택 후 "플레이어 열기" → `/player/{job_id}`

### `app/player/[jobId]/page.tsx` — 플레이어 페이지

```
로딩 → fetchOverlay(/api/backend/overlay/{jobId}) →
  200: ready → VideoPlayer 렌더링
  202: polling → fetchStatus(/api/backend/jobs/{jobId}) → 5초마다 재시도
  실패: error 표시
```

**폴링 상태**: `loading` → `polling(status)` → `ready(metadata)` / `error`

### `components/VideoPlayer.tsx`

- 메인 VOD 영상 재생 (`original_video_url` 직접 접근)
- `isPlaying` 상태 추적 → `AdOverlay`에 전달 (영상 일시정지 시 광고도 정지)
- `isEnded` → 영상 종료 후 overlay 숨김
- 현재 시각 기준 활성 overlay 필터링 → score 최고 1개만 렌더링
- 오버레이 목록 표시 (seekTo 연동)
- `crossOrigin` 속성 없음 (FileResponse CORS 간섭 방지)

### `components/AdOverlay.tsx`

- `isPlaying` prop: 메인 영상 재생 상태와 광고 영상 동기화
- 위치: `coordinates_x/y/w/h` 기준 절대 좌표

### `types/overlay.ts` — 타입 정의

```typescript
interface OverlayMetadata {
  job_id: string;
  original_video_url: string;
  total_duration_sec: number;
  overlays: OverlayItem[];
}

interface OverlayItem {
  decision_id: number;
  matched_ad_id: string;
  ad_resource_url: string;
  ad_type: "video_clip" | "banner";
  overlay_start_time_sec: number;
  overlay_duration_sec: number;
  coordinates_x: number;
  coordinates_y: number;
  coordinates_w: number;
  coordinates_h: number;
  score: number;
}
```

---

## 10. Step 5 REST API

**파일**: `step5_api/server.py` | **포트**: 8000

### 엔드포인트 목록

| Method | 경로 | 역할 |
|--------|------|------|
| GET | `/vod/files` | VOD 디렉토리 영상 파일 목록 (드롭다운용) |
| POST | `/jobs` | 분석 작업 제출 → job_id 반환 |
| GET | `/jobs/completed` | 완료된 작업 목록 (최신순, 드롭다운용) |
| GET | `/jobs/{job_id}` | 작업 상태 조회 |
| GET | `/overlay/{job_id}` | 오버레이 메타데이터 (완료: 200, 미완료: 202) |
| POST | `/feedback/{decision_id}` | 광고 배치 적합성 피드백 (-1/0/1) |
| GET | `/media/source/{filename:path}` | 원본 VOD 스트리밍 (Range request 지원) |
| GET | `/media/ads/videos/...` | 광고 영상 스트리밍 (StaticFiles) |
| GET | `/media/ads/images/...` | 광고 이미지 스트리밍 (StaticFiles) |
| GET | `/media/jobs/...` | 분석 산출물 스트리밍 (StaticFiles) |

### 주요 구현 사항

**`/vod/files`**: `VOD_DIR` 환경변수 경로에서 영상 파일 목록 반환
확장자 필터: `.mp4 .mkv .avi .mov .ts .m4v`

**`/jobs/completed`**: `status = 'complete'` 조건으로 `updated_at DESC` 정렬
응답: `{ job_id, filename(경로 제외), updated_at }`

**`/overlay/{job_id}`**:
- Python 레벨 중복 start_time 제거 + greedy 겹침 제거
- `original_video_filename` = `input_video_path.replace("\\", "/").split("/")[-1]`
  (DB에 Windows 경로 저장 시 파일명만 추출)
- `original_video_url` = `{API_BASE_URL}/media/source/{quote(filename)}`

**`/media/source/{filename:path}`**:
`FileResponse` 사용 (StaticFiles 한국어 파일명 미지원 우회)
```python
decoded = unquote(filename)   # %EB%AC%B4... → 무명전설...
path = Path(_vod_dir) / decoded
return FileResponse(str(path), media_type="video/mp4")
```

**`/feedback/{decision_id}`**: `label` -1/0/1, `ON CONFLICT DO UPDATE` (UPSERT)

---

## 11. 스코어링 로직

### 점수 항목

| 항목 | 점수 | 조건 |
|------|------|------|
| **[1차 필터]** Narrative 유사도 | Skip | similarity < 0.30 |
| **[2차 필터]** 물리적 수용성 | Skip | video_clip AND scene < ad 길이 |
| Narrative 유사도 (Primary) | +0~+80 | similarity ≥ 0.25 → scaled |
| Narrative 유사도 (Fallback) | +0~+80 | target_narrative 없을 때만 |
| 빈 화면 | +20 | 최적 윈도우 avg_density ≤ 0.3 |
| 침묵 가점 | +15 | 최적 윈도우 ∩ 침묵 구간 존재 |
| 복잡한 화면 | -40 | 최적 윈도우 avg_density ≥ 0.7 |

### 유사도 계산

```
[Primary]
similarity = cosine_similarity(embed(context_narrative), embed(target_narrative))

[Fallback]
similarity = 0.7 × sim(context_narrative, ad_name + target_mood)
           + 0.3 × sim(context_narrative, target_mood)

[점수 변환]
semantic_score = 0                               if similarity < 0.25
               = (similarity - 0.25) / 0.75 × 80  if similarity ≥ 0.25
```

### 슬라이딩 윈도우

```
1. vision_context에서 씬 범위 내 프레임 전체 로드
2. t = scene_start, 1초씩 슬라이딩:
     avg_density = mean(object_density for frame in window)
     safe_px     = 교집합 safe_area 넓이
3. 최적 = argmax(safe_px), tie-break: argmin(avg_density)
4. overlay_start_time_sec = best_t
   coordinates = 최적 윈도우의 safe_area 교집합 (x, y, w, h)
5. vision 데이터 없으면 scene_start_sec 기본값
```

### 선택 규칙

1. `score ≤ 0` 후보 제거
2. 동일 `scene_start_sec` 내 최고점 1개 선택
3. 서로 다른 씬 간 `overlay_start_time_sec` 시간 겹침 greedy 제거
4. INSERT 전 기존 `decision_result` DELETE (재실행 중복 방지)
5. Step 5 API 추가 필터: Python 레벨 중복 start_time + greedy 겹침 재검증

---

## 12. 광고 분석 서비스 (analyze-narrative)

**목적**: `ad_inventory.target_narrative`를 Qwen2-VL로 생성하는 사전 준비 서비스
**배포 위치**: `/app/Docker/analyze-narrative/`

### 파일 구조

```
/app/Docker/analyze-narrative/
  analyze_ad_narrative.py
  Dockerfile.analyze-narrative
  docker-compose.analyze-narrative.yml
  requirements.analyze-narrative.txt
  common/ {config, db, logging_setup, __init__}.py
```

### 볼륨 마운트

| 호스트 | 컨테이너 | 용도 |
|--------|---------|------|
| `/app/HelloVision/data/ad_assets/video` | `/ads/video:ro` | 광고 영상 |
| `/app/HelloVision/data/ad_assets/image` | `/ads/banner:ro` | 광고 이미지 |
| `/app/HelloVision/data/logs` | `/app/storage/logs` | 로그 |
| `qwen_model_cache` (named) | `/models` | Qwen2-VL 캐시 (~4.5GB) |

### `target_narrative` 4차원 서술문 형식

```
"A leading automotive brand targeting young professionals and driving enthusiasts,
promising exhilarating performance and cutting-edge technology,
delivered with a bold and dynamic tone."

4개 차원:
1. Category   — 산업군/제품군
2. Audience   — 타겟 고객 (연령·성별·관심사)
3. Core Message — 핵심 가치
4. Ad Vibe    — 광고 분위기
```

### 경로 변환 (`_resolve_path()`)

DB에 저장된 Windows 경로 → Linux 컨테이너 경로 자동 변환

```python
def _resolve_path(resource_path: str, ad_type: str) -> str:
    if len(resource_path) >= 3 and resource_path[1] == ":":  # Windows 경로 감지
        filename = resource_path.replace("\\", "/").split("/")[-1]
        base_dir = AD_VIDEO_DIR if ad_type == "video_clip" else AD_IMAGE_DIR
        return str(Path(base_dir) / filename)
    return resource_path
```

### 실행 명령어

```bash
cd /app/Docker/analyze-narrative

# 이미지 빌드
docker-compose -f docker-compose.analyze-narrative.yml build

# 미처리 목록 확인 (실제 분석 없음)
docker-compose -f docker-compose.analyze-narrative.yml run --rm analyze-narrative --dry-run

# 일부 테스트 (10개)
docker-compose -f docker-compose.analyze-narrative.yml run --rm analyze-narrative --limit 10

# 전체 실행 (백그라운드)
nohup docker-compose -f docker-compose.analyze-narrative.yml run --rm analyze-narrative \
    > /app/HelloVision/data/logs/docker-run.log 2>&1 &

# 진행 확인
tail -f /app/HelloVision/data/logs/analyze_ad_narrative.log
```

**멱등성**: `target_narrative IS NULL` 조건으로 미처리 광고만 선택 → 중단 후 재실행 시 자동 이어서 처리

---

## 13. 유틸리티 스크립트

| 스크립트 | 용도 | 실행 시점 |
|---------|------|---------|
| `init_db.py` | DB 스키마 초기화 (전체 테이블 생성) | 최초 1회 |
| `populate_ad_inventory.py` | 광고 자산 DB 등록 | 광고 추가 시 |
| `migrate_add_context_tags.py` | `context_tags`, `context_summary` 컬럼 추가 | v2.1/2.2 업그레이드 |
| `migrate_add_target_narrative.py` | `ad_inventory.target_narrative` 컬럼 추가 | v2.5 업그레이드 |
| `migrate_add_analysis_scene.py` | `analysis_scene` 테이블 생성 | v2.5 업그레이드 |
| `backfill_context_tags.py` | 기존 job에 context 재생성 | 마이그레이션 후 기존 데이터 보완 |
| `test_vision_yolo.py` | YOLOv8l 단위 테스트 (32개) | 개발 검증 |

---

## 14. 함수 전체 목록

### Backend

| 파일 | 함수 | 역할 |
|------|------|------|
| `step1_preprocessing/pipeline.py` | `extract_audio` | ffmpeg WAV 추출 |
| `step1_preprocessing/pipeline.py` | `extract_frames` | ffmpeg JPEG 추출 |
| `step1_preprocessing/pipeline.py` | `get_video_metadata` | ffprobe 메타데이터 |
| `step1_preprocessing/pipeline.py` | `run` | Step 1 실행 |
| `step2_analysis/vision_yolo.py` | `_get_model` | YOLOv8l 로드 |
| `step2_analysis/vision_yolo.py` | `_compute_safe_area` | safe_area + density + exclusion zone |
| `step2_analysis/vision_yolo.py` | `_is_scene_cut` | 장면 전환 감지 |
| `step2_analysis/vision_yolo.py` | `analyse_frames` | 배치 프레임 분석 |
| `step2_analysis/dialogue_segmenter.py` | `segment_video` | 전방향 씬 경계 탐지 → 씬 목록 |
| `step2_analysis/dialogue_segmenter.py` | `find_context_start` | 가변 컨텍스트 윈도우 시작 시각 |
| `step2_analysis/vision_qwen.py` | `analyse_scene_context` | 씬 전체 → 1-2문장 context_narrative |
| `step2_analysis/vision_qwen.py` | `_clean_vlm_response` | VLM 출력 정규화 |
| `step2_analysis/vision_qwen.py` | `_describe_frame` | 단일 프레임 → 장면 설명 |
| `step2_analysis/vision_qwen.py` | `analyse_frames` | 샘플 프레임 분석 |
| `step2_analysis/audio_analysis.py` | `detect_silence` | 침묵 구간 추출 |
| `step2_analysis/audio_transcription.py` | `transcribe` | 음성 → 텍스트 |
| `step2_analysis/consumer.py` | `_sample_frames_for_scene` | 씬 범위 균등 n프레임 샘플링 |
| `step2_analysis/consumer.py` | `_insert_scene_context` | analysis_scene INSERT (ON CONFLICT UPDATE) |
| `step2_analysis/consumer.py` | `_assign_scene_context_to_silences` | 침묵 → 씬 context 매핑 + analysis_audio UPDATE |
| `step2_analysis/consumer.py` | `_generate_scene_contexts` | Phase A 전체 오케스트레이션 |
| `step2_analysis/consumer.py` | `run` | Step 2 전체 실행 |
| `step3_persistence/pipeline.py` | `_get_scene_intervals` | analysis_scene 씬 목록 조회 |
| `step3_persistence/pipeline.py` | `_get_ad_inventory` | 광고 목록 조회 |
| `step3_persistence/pipeline.py` | `build_candidates` | 씬 × 광고 후보 쌍 생성 |
| `step3_persistence/pipeline.py` | `run` | Step 3 실행 |
| `step4_decision/embedding_scorer.py` | `score_narrative_fit` | narrative 1:1 코사인 유사도 (주 경로) |
| `step4_decision/embedding_scorer.py` | `score_ad_context_fit` | 레거시 fallback 앙상블 유사도 |
| `step4_decision/embedding_scorer.py` | `compute_similarity` | 코사인 유사도 |
| `step4_decision/embedding_scorer.py` | `embed` | 정규화 임베딩 벡터 |
| `step4_decision/embedding_scorer.py` | `is_available` | 모델 로드 여부 |
| `step4_decision/scoring.py` | `_get_scene_frames` | 씬 범위 vision 프레임 전체 로드 |
| `step4_decision/scoring.py` | `_intersect_safe_areas` | 다중 프레임 safe_area 교집합 |
| `step4_decision/scoring.py` | `_find_best_overlay_window` | 1초 슬라이딩 윈도우 최적 구간 탐색 |
| `step4_decision/scoring.py` | `_get_silence_overlap` | 침묵 가점 여부 |
| `step4_decision/scoring.py` | `_compute_score` | 1차→2차→3차 필터 + 점수 산출 |
| `step4_decision/scoring.py` | `_pick_best_and_deduplicate` | 씬당 최고점 + greedy 겹침 제거 |
| `step4_decision/scoring.py` | `_insert_decision_results` | DELETE-before-INSERT |
| `step4_decision/scoring.py` | `run` | Step 4 실행 |
| `step5_api/server.py` | `list_vod_files` | GET /vod/files — VOD 파일 목록 |
| `step5_api/server.py` | `submit_job` | POST /jobs — 작업 제출 |
| `step5_api/server.py` | `list_completed_jobs` | GET /jobs/completed — 완료 작업 목록 |
| `step5_api/server.py` | `get_job_status` | GET /jobs/{id} — 상태 조회 |
| `step5_api/server.py` | `get_overlay_metadata` | GET /overlay/{id} — 오버레이 메타데이터 |
| `step5_api/server.py` | `submit_feedback` | POST /feedback/{id} — 피드백 저장 |
| `step5_api/server.py` | `serve_source_video` | GET /media/source/{filename} — VOD 스트리밍 |
| `analyze_ad_narrative.py` | `_resolve_path` | Windows DB경로 → Linux 컨테이너 경로 변환 |
| `analyze_ad_narrative.py` | `_clean_vlm_response` | VLM 출력 정규화 |
| `analyze_ad_narrative.py` | `_extract_video_frame` | 영상 33% 지점 프레임 추출 |
| `analyze_ad_narrative.py` | `_analyse_ad` | Qwen2-VL 4차원 narrative 생성 |
| `analyze_ad_narrative.py` | `_get_unprocessed_ads` | 미처리 광고 조회 (멱등성) |
| `analyze_ad_narrative.py` | `_process_ad` | 광고 유형별 분기 + VLM 분석 |
| `analyze_ad_narrative.py` | `run` | 일괄 처리 메인 |

### Frontend

| 파일 | 컴포넌트/함수 | 역할 |
|------|------------|------|
| `app/page.tsx` | `HomePage` | VOD 선택 드롭다운 + 완료 작업 드롭다운 |
| `app/player/[jobId]/page.tsx` | `PlayerPage` | 플레이어 + 폴링 + StatusBadge |
| `components/VideoPlayer.tsx` | `VideoPlayer` | 플레이어 + 오버레이 + 목록 |
| `components/AdOverlay.tsx` | `AdOverlay` | 단일 광고 오버레이 (isPlaying 동기화) |
| `types/overlay.ts` | `OverlayMetadata`, `OverlayItem` | 타입 정의 |
| `next.config.js` | rewrites | `/api/backend/*` → backend 프록시 |
