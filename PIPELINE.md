# VOD Ad Overlay System — 파이프라인 전체 문서

> **2026_VOD_FAST_4** | 비디오 문맥 분석 기반 동적 광고 오버레이 시스템

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [데이터베이스 스키마](#2-데이터베이스-스키마)
3. [RabbitMQ 큐 구조](#3-rabbitmq-큐-구조)
4. [파이프라인 단계별 상세](#4-파이프라인-단계별-상세)
   - [Step 1: Preprocessing](#41-step-1--preprocessing)
   - [Step 2: Multimodal Analysis](#42-step-2--multimodal-analysis)
   - [Step 3: Candidate Building](#43-step-3--candidate-building)
   - [Step 4: Scoring & Decision](#44-step-4--scoring--decision)
   - [Step 5: REST API](#45-step-5--rest-api)
5. [공통 모듈](#5-공통-모듈)
6. [프론트엔드](#6-프론트엔드-nextjs)
7. [데이터 흐름 (End-to-End)](#7-데이터-흐름-end-to-end)
8. [파일 I/O 패턴](#8-파일-io-패턴)
9. [스코어링 로직](#9-스코어링-로직)
10. [함수 전체 목록](#10-함수-전체-목록)

---

## 1. 시스템 개요

4단계 AI 분석 파이프라인이 RabbitMQ를 통해 비동기로 연결되며, PostgreSQL에 데이터를 축적한 뒤 Next.js 프론트엔드에서 광고를 실시간 오버레이한다.

```
[사용자: 영상 경로 입력]
        |
  Step 1: Preprocessing    ffmpeg로 프레임/오디오 추출
        |
  Step 2: Analysis         R-CNN + Qwen2-VL + Whisper + librosa 분석
        |
  Step 3: Candidates       (침묵 구간 × 광고) 후보 쌍 생성
        |
  Step 4: Scoring          스코어링 → 침묵 구간당 최고점 광고 1개 선택
        |
  Step 5: API              오버레이 메타데이터 제공
        |
  [프론트엔드: 비디오 + 광고 오버레이 재생]
```

### 기술 스택

| 영역 | 기술 |
|------|------|
| 언어/프레임워크 | Python 3.x, FastAPI, Next.js 14 |
| 메시지 브로커 | RabbitMQ (pika) |
| 데이터베이스 | PostgreSQL (psycopg2) |
| AI 모델 | Faster R-CNN (torchvision), Qwen2-VL-2B-Instruct, OpenAI Whisper, librosa |
| 미디어 처리 | ffmpeg, ffprobe |
| 인프라 | PostgreSQL: 121.167.223.17:5432 / RabbitMQ: 121.167.223.17:5672 |

---

## 2. 데이터베이스 스키마

### 테이블 목록

| 테이블 | 역할 | 생성 단계 |
|--------|------|---------|
| `job_history` | 작업 메타데이터 및 상태 추적 | 수동 초기화 |
| `video_preprocessing_info` | Step 1 추출 결과 | Step 1 |
| `analysis_vision_context` | R-CNN + Qwen2-VL 프레임 분석 | Step 2 |
| `analysis_audio` | 음성 침묵 구간 | Step 2 |
| `analysis_transcript` | Whisper STT 자막 | Step 2 |
| `ad_inventory` | 광고 자산 카탈로그 | 수동 초기화 |
| `decision_result` | 최종 광고 삽입 결정 | Step 4 |

### 테이블 스키마 상세

#### `job_history`
```sql
job_id          UUID PRIMARY KEY
status          TEXT          -- pending | preprocessing | analysing | complete | failed
input_video_path TEXT
error_message   TEXT
created_at      TIMESTAMP
updated_at      TIMESTAMP
```

#### `video_preprocessing_info`
```sql
id              SERIAL PRIMARY KEY
job_id          UUID REFERENCES job_history
original_video_path TEXT
audio_path      TEXT          -- storage/jobs/{job_id}/audio.wav
frame_dir_path  TEXT          -- storage/jobs/{job_id}/frames/
duration_sec    FLOAT
fps             FLOAT
width           INT
height          INT
total_frames    INT
created_at      TIMESTAMP
```

#### `analysis_vision_context`
```sql
id              SERIAL PRIMARY KEY
job_id          UUID REFERENCES job_history
frame_index     INT
timestamp_sec   FLOAT
safe_area_x     INT           -- 광고 배치 가능한 빈 영역 (원본 해상도)
safe_area_y     INT
safe_area_w     INT
safe_area_h     INT
object_density  FLOAT         -- 0.0~1.0 (객체가 점유하는 화면 비율)
scene_description TEXT        -- Qwen2-VL 생성 장면 설명 (영어)
is_scene_cut    BOOLEAN       -- 장면 전환 여부
created_at      TIMESTAMP

UNIQUE(job_id, frame_index)   -- 멱등성 보장
```

#### `analysis_audio`
```sql
id              SERIAL PRIMARY KEY
job_id          UUID REFERENCES job_history
silence_start_sec FLOAT
silence_end_sec   FLOAT
duration_sec    FLOAT GENERATED ALWAYS AS (silence_end_sec - silence_start_sec)
created_at      TIMESTAMP

UNIQUE(job_id, silence_start_sec, silence_end_sec)
```

#### `analysis_transcript`
```sql
id              SERIAL PRIMARY KEY
job_id          UUID REFERENCES job_history
start_sec       FLOAT
end_sec         FLOAT
text            TEXT          -- Whisper 변환 텍스트 (영어)
created_at      TIMESTAMP

UNIQUE(job_id, start_sec, end_sec)
```

#### `ad_inventory`
```sql
ad_id           TEXT PRIMARY KEY
ad_name         TEXT
ad_type         TEXT          -- video_clip | banner
resource_path   TEXT          -- 실제 파일 경로
duration_sec    FLOAT         -- 영상 광고만 해당
target_mood     TEXT[]        -- 키워드 배열 (예: ['sports', 'energy', 'fitness'])
```

#### `decision_result`
```sql
id              SERIAL PRIMARY KEY
job_id          UUID REFERENCES job_history
ad_id           TEXT REFERENCES ad_inventory
overlay_start_time_sec  FLOAT
overlay_duration_sec    FLOAT
coordinates_x   INT           -- safe_area 기반 (원본 해상도)
coordinates_y   INT
coordinates_w   INT
coordinates_h   INT
score           INT
created_at      TIMESTAMP
```

---

## 3. RabbitMQ 큐 구조

### 큐 흐름

```
QUEUE_STEP1: vod.step1.preprocess
       |
QUEUE_STEP2: vod.step2.analysis
       |
QUEUE_STEP3: vod.step3.persistence
       |
QUEUE_STEP4: vod.step4.decision
```

### 메시지 페이로드

| 큐 | 발행자 | 구독자 | 페이로드 |
|----|--------|--------|---------|
| `vod.step1.preprocess` | API (`/jobs`) | Step 1 | `{"job_id": str, "video_path": str}` |
| `vod.step2.analysis` | Step 1 | Step 2 | `{"job_id": str}` |
| `vod.step3.persistence` | Step 2 | Step 3 | `{"job_id": str}` |
| `vod.step4.decision` | Step 3 | Step 4 | `{"job_id": str, "candidates": list[dict]}` |

### 내구성 설정

- **durable=True**: 브로커 재시작 시 큐 유지
- **delivery_mode=2**: 영속성 메시지 (디스크 저장)
- **prefetch=1**: 한 번에 1개 메시지만 처리
- **ack_early=True**: Step 1, 2에서 콜백 전 ack 전송 (긴 처리 시간 시 consumer_timeout 방지)

---

## 4. 파이프라인 단계별 상세

---

### 4.1 Step 1 — Preprocessing

**파일**: `backend/step1_preprocessing/pipeline.py`
**목적**: 입력 영상을 프레임(JPEG)과 오디오(WAV)로 분리하고 메타데이터를 DB에 저장

#### 함수 목록

| 함수 | 입력 | 출력 | 역할 |
|------|------|------|------|
| `_job_storage_dir(job_id)` | UUID | `Path` | 작업 저장소 디렉토리 생성 |
| `_update_job_status(job_id, status, error)` | UUID, str | - | `job_history.status` UPDATE |
| `extract_audio(video_path, output_dir)` | str, Path | Path | ffmpeg로 WAV 추출 |
| `extract_frames(video_path, output_dir, fps)` | str, Path, int | list[Path] | ffmpeg로 JPEG 프레임 추출 |
| `get_video_metadata(video_path)` | str | dict | ffprobe로 duration/fps/해상도 추출 |
| `save_to_db(job_id, paths, meta)` | UUID, dict, dict | - | `video_preprocessing_info` INSERT |
| `run(job_id, video_path)` | UUID, str | - | Step 1 전체 실행 |
| `_on_message(payload)` | dict | - | RabbitMQ 콜백 |

#### 처리 흐름

```
1. job_history UPDATE → status='preprocessing'
2. _job_storage_dir() → D:\storage\jobs\{job_id}\ 생성
3. extract_audio()
   - ffmpeg -i {video} -ar 16000 -ac 1 -c:a pcm_s16le audio.wav
4. extract_frames()
   - ffmpeg -i {video} -vf fps=1 frames/frame_%04d.jpg
5. get_video_metadata()
   - ffprobe -v quiet -print_format json -show_streams
6. save_to_db() → video_preprocessing_info INSERT
7. job_history UPDATE → status='analysing'
8. PUBLISH → QUEUE_STEP2
```

#### 생성 파일

```
D:\20.WORKSPACE\2026_VOD_FAST_4\storage\jobs\{job_id}\
  audio.wav               16kHz, mono, PCM s16le
  frames\
    frame_0001.jpg        1fps 추출 (JPEG)
    frame_0002.jpg
    ...
```

#### DB 작업

| 작업 | 테이블 | 조건 |
|------|--------|------|
| UPDATE | `job_history` | status = preprocessing / analysing / failed |
| INSERT | `video_preprocessing_info` | job_id당 1회 |

---

### 4.2 Step 2 — Multimodal Analysis

**파일**: `backend/step2_analysis/` (4개 파일)
**목적**: Faster R-CNN(객체 감지) + Qwen2-VL(장면 설명) + Whisper(STT) + librosa(침묵 감지)를 병렬 실행하여 분석 데이터를 DB에 저장

---

#### 4.2.1 `vision_rcnn.py` — Faster R-CNN 분석

**모델**: `fasterrcnn_resnet50_fpn` (COCO 사전학습, torchvision)

| 함수 | 역할 |
|------|------|
| `_get_model()` | ResNet-50 FPN 로드 (GPU/CPU 자동 선택) |
| `_largest_safe_rectangle(occupied_mask)` | 히스토그램 알고리즘으로 객체 없는 최대 사각형 찾기 |
| `_compute_safe_area(frame_shape, boxes)` | safe_area(x,y,w,h) + object_density 계산 |
| `_is_scene_cut(prev_gray, curr_gray)` | 평균 픽셀 차이 > 30.0 이면 장면 전환 판정 |
| `analyse_frames(frame_paths, on_batch, batch_size)` | 배치 스트리밍 처리 (배치마다 on_batch 콜백) |

**처리 로직**
```
FOR EACH frame:
  1. torchvision Faster R-CNN 추론
  2. score > 0.5 bbox 필터링
  3. _compute_safe_area():
     - 바이너리 마스크에 bbox 채우기
     - _largest_safe_rectangle()로 최대 빈 영역 찾기
     - object_density = Σ(bbox areas) / (width * height)
  4. _is_scene_cut(): 이전 프레임 gray와 비교
  5. 200 프레임마다 on_batch(results) 콜백 실행
```

---

#### 4.2.2 `vision_qwen.py` — Qwen2-VL 장면 설명

**모델**: `Qwen/Qwen2-VL-2B-Instruct` (HuggingFace)

| 함수 | 역할 |
|------|------|
| `_get_model()` | AutoProcessor + Qwen2VLForConditionalGeneration 로드 |
| `_describe_frame(frame_path)` | 단일 프레임 → 영어 장면 설명 (3문장 이하) |
| `_compute_sample_interval(total_frames)` | 적응형 샘플링 간격 계산 |
| `analyse_frames(frame_paths)` | `{frame_index: description}` 반환 |

**프롬프트**
```
"Describe the scene, mood, and situation in this video frame concisely in English.
Include the main subjects, background setting, and overall emotional tone.
Keep your response to 3 sentences or fewer."
```

**샘플링 전략**
```
base_samples = ceil(total_frames / 60초)
if base_samples > 60:          # 최대 60개 제한
    interval = ceil(total_frames / 60)
else:
    interval = 60

# 예: 3600프레임(1시간) → interval=60 → 60개 분석
# 예: 7200프레임(2시간) → interval=120 → 60개 분석
```

---

#### 4.2.3 `audio_analysis.py` — 침묵 감지

**라이브러리**: librosa

| 함수 | 역할 |
|------|------|
| `detect_silence(audio_path)` | RMS 기반 침묵 구간 추출 |

**파라미터**

| 설정 | 값 | 의미 |
|------|----|----|
| frame_length | 25ms | RMS 계산 윈도우 |
| hop_length | 10ms | 슬라이딩 간격 |
| threshold | -40 dB | 침묵 판정 기준 |
| min_duration | 1.0초 | 최소 침묵 길이 |

---

#### 4.2.4 `audio_transcription.py` — Whisper STT

**모델**: OpenAI Whisper `base` (기본)

| 함수 | 역할 |
|------|------|
| `_load_model(model_name)` | Whisper 모델 로드 및 캐싱 |
| `transcribe(audio_path)` | 오디오 → 세그먼트 목록 반환 |

**출력 형식**
```python
[
  {"start_sec": 0.0, "end_sec": 3.5, "text": "Welcome to today's workout"},
  {"start_sec": 3.5, "end_sec": 7.2, "text": "Let's begin with stretching"},
  ...
]
```

> **task='translate'**: 비영어 음성도 영어로 변환 → ad `target_mood` 키워드 매칭 정확도 향상

---

#### 4.2.5 `consumer.py` — Step 2 오케스트레이터

| 함수 | 역할 |
|------|------|
| `_insert_vision_batch(job_id, rows)` | R-CNN 배치 → `analysis_vision_context` INSERT |
| `_update_scene_descriptions(job_id, descriptions, total_frames)` | Qwen 결과 → 범위 UPDATE |
| `_insert_audio_intervals(job_id, intervals)` | 침묵 구간 → `analysis_audio` INSERT |
| `_insert_transcript(job_id, segments)` | 자막 → `analysis_transcript` INSERT |
| `_update_job_status(job_id, status, error)` | `job_history` UPDATE |
| `_already_processed(job_id)` | 멱등성 확인 (이미 분석된 job 건너뜀) |
| `run(job_id)` | Step 2 전체 실행 |
| `_on_message(payload)` | RabbitMQ 콜백 |

**DB 작업**

| 작업 | 테이블 | 내용 |
|------|--------|------|
| SELECT | `video_preprocessing_info` | frame_dir, audio_path 조회 |
| INSERT | `analysis_vision_context` | 200프레임 배치 (ON CONFLICT DO NOTHING) |
| UPDATE | `analysis_vision_context` | scene_description 범위 UPDATE (Qwen 결과) |
| INSERT | `analysis_audio` | 침묵 구간 (ON CONFLICT DO NOTHING) |
| INSERT | `analysis_transcript` | 자막 세그먼트 (ON CONFLICT DO NOTHING) |
| UPDATE | `job_history` | status 갱신 |
| PUBLISH | QUEUE_STEP3 | `{"job_id": str}` |

**Qwen 결과 범위 UPDATE 방식**
```
Qwen이 frame_index=0, 60, 120, 180... 만 분석함
→ 나머지 프레임은 가장 가까운 Qwen 샘플의 설명을 inherited

UPDATE analysis_vision_context
  SET scene_description = ...
  WHERE job_id = ...
    AND frame_index >= start_idx
    AND frame_index < end_idx
```

---

### 4.3 Step 3 — Candidate Building

**파일**: `backend/step3_persistence/pipeline.py`
**목적**: 모든 침묵 구간과 모든 광고를 조합하여 후보 쌍을 생성하고 Step 4로 전달

#### 함수 목록

| 함수 | 역할 |
|------|------|
| `_update_job_status(job_id, status, error)` | `job_history` UPDATE |
| `_get_silence_intervals(job_id)` | `analysis_audio` 전체 조회 |
| `_get_ad_inventory()` | `ad_inventory` 전체 조회 |
| `build_candidates(job_id)` | (침묵 × 광고) Cartesian product |
| `run(job_id)` | Step 3 전체 실행 |
| `_on_message(payload)` | RabbitMQ 콜백 |

#### 후보 구조

```python
# 침묵 N개 × 광고 M개 = N*M개 후보
{
    "silence_start_sec": 45.0,
    "silence_end_sec": 48.5,
    "silence_duration": 3.5,
    "ad_id": "cf_042",
    "ad_type": "video_clip",     # or "banner"
    "ad_duration_sec": 15.0,
    "target_mood": ["sports", "energy", "fitness"],
}
```

#### DB 작업

| 작업 | 테이블 |
|------|--------|
| SELECT | `analysis_audio` |
| SELECT | `ad_inventory` |
| PUBLISH | QUEUE_STEP4 (candidates 리스트 포함) |

> DB 쓰기 없음 — 후보는 RabbitMQ 메시지에 임베드하여 전달

---

### 4.4 Step 4 — Scoring & Decision

**파일**: `backend/step4_decision/scoring.py`
**목적**: 각 후보를 스코어링하여 침묵 구간당 최고점 광고 1개를 선택하고 `decision_result`에 저장

#### 함수 목록

| 함수 | 역할 |
|------|------|
| `_update_job_status(job_id, status, error)` | `job_history` UPDATE |
| `_get_vision_context_near(job_id, timestamp_sec)` | 가장 가까운 프레임의 vision 데이터 조회 |
| `_get_transcript_text(job_id, start_sec, end_sec)` | 해당 구간의 자막 합치기 |
| `_check_recent_cut(job_id, timestamp_sec, window_sec=2.0)` | 2초 내 장면 전환 여부 확인 |
| `_compute_score(candidate, vision, recent_cut, transcript_text)` | 스코어 계산 |
| `_pick_best_per_interval(scored)` | 침묵 구간당 최고점 1개 선택 |
| `_insert_decision_results(job_id, results)` | `decision_result` INSERT |
| `run(job_id, candidates)` | Step 4 전체 실행 |
| `_on_message(payload)` | RabbitMQ 콜백 |

#### 스코어링 공식

```
score = 0

[긍정 요소]
+ 30  침묵 길이 >= 광고 길이           (광고 전체 재생 가능)
+ 20  2초 내 장면 전환 존재            (자연스러운 전환점)
+ 20  object_density <= 0.3           (빈 공간 충분)
+ 10  scene_description 키워드 매치    (per matched keyword)
+ 10  transcript_text 키워드 매치      (per matched keyword)

[부정 요소]
- 40  object_density >= 0.7           (화면이 너무 복잡함)

[필터]
score <= 0 → 후보 제외
침묵당 최고점 1개만 선택
```

#### 오버레이 지속 시간 계산

```python
if ad_type == "video_clip":
    overlay_duration = ad_duration_sec        # 광고 전체 재생
else:  # banner
    overlay_duration = min(ad_duration, silence_duration)  # 침묵 구간 동안만
```

#### DB 작업

| 작업 | 테이블 | 내용 |
|------|--------|------|
| SELECT | `analysis_vision_context` | 가장 가까운 프레임 |
| SELECT | `analysis_transcript` | 구간 내 자막 |
| INSERT | `decision_result` | 최종 결정 (score > 0) |
| UPDATE | `job_history` | status='complete' |

---

### 4.5 Step 5 — REST API

**파일**: `backend/step5_api/server.py`
**프레임워크**: FastAPI + Uvicorn
**포트**: 8000

#### API 엔드포인트

| Method | 경로 | 역할 | 반환 |
|--------|------|------|------|
| POST | `/jobs` | 작업 제출 | `{"job_id": str, "status": str}` |
| GET | `/jobs/{job_id}` | 작업 상태 조회 | JobStatusResponse |
| GET | `/overlay/{job_id}` | 오버레이 메타데이터 | OverlayMetadata |
| GET | `/media/jobs/{job_id}/...` | 프레임/오디오 스트리밍 | StaticFiles |
| GET | `/media/ads/videos/...` | 광고 영상 스트리밍 | StaticFiles |
| GET | `/media/ads/images/...` | 광고 이미지 스트리밍 | StaticFiles |
| GET | `/media/source/{filename}` | 원본 영상 스트리밍 | FileResponse |

#### 함수 목록

| 함수 | 역할 |
|------|------|
| `_ad_url(ad_type, resource_path)` | 광고 파일 → URL 변환 |
| `submit_job(body)` | `job_history` INSERT + QUEUE_STEP1 발행 |
| `get_job_status(job_id)` | `job_history` 조회 |
| `get_overlay_metadata(job_id)` | `job_history` + `video_preprocessing_info` + `decision_result` JOIN |
| `serve_source_video(filename)` | 원본 영상 파일 FileResponse |

#### 정적 파일 마운트

```python
"/media/jobs"       → D:\20.WORKSPACE\2026_VOD_FAST_4\storage\jobs
"/media/ads/videos" → D:\20.WORKSPACE\2026_VOD_FAST_3\TV_CF\output
"/media/ads/images" → D:\20.WORKSPACE\2026_VOD_FAST_3\TV_CF\output_print
```

#### `/overlay/{job_id}` 응답 구조

```json
{
  "job_id": "uuid",
  "original_video_url": "http://localhost:8000/media/source/video.mp4",
  "total_duration_sec": 180.5,
  "overlays": [
    {
      "matched_ad_id": "cf_042",
      "ad_resource_url": "http://localhost:8000/media/ads/videos/광고.mp4",
      "ad_type": "video_clip",
      "overlay_start_time_sec": 45.0,
      "overlay_duration_sec": 15.0,
      "coordinates_x": 1400,
      "coordinates_y": 50,
      "coordinates_w": 480,
      "coordinates_h": 270,
      "score": 62
    }
  ]
}
```

---

## 5. 공통 모듈

### `backend/common/config.py`

환경 변수 기반 전역 설정. 모든 Step에서 임포트한다.

| 변수 | 기본값 | 용도 |
|------|--------|------|
| `DB_HOST` | 121.167.223.17 | PostgreSQL 호스트 |
| `DB_PORT` | 5432 | |
| `DB_NAME` | hv02 | |
| `DB_USER` | postgres01 | |
| `DB_PASSWORD` | postgres01 | |
| `RABBITMQ_HOST` | 121.167.223.17 | |
| `RABBITMQ_PORT` | 5672 | |
| `RABBITMQ_USER` | admin | |
| `RABBITMQ_PASSWORD` | admin | |
| `AD_VIDEO_DIR` | ..\2026_VOD_FAST_3\TV_CF\output | 광고 영상 디렉토리 |
| `AD_IMAGE_DIR` | ..\2026_VOD_FAST_3\TV_CF\output_print | 광고 이미지 디렉토리 |
| `STORAGE_BASE` | ..\2026_VOD_FAST_4\storage | 작업 저장소 루트 |
| `FRAME_EXTRACTION_FPS` | 1 | Step 1 프레임 추출 속도 |
| `SCENE_CUT_THRESHOLD` | 30.0 | R-CNN 장면 전환 감지 임계값 |
| `SILENCE_THRESHOLD_DB` | -40.0 | librosa 침묵 기준 (dB) |
| `MIN_SILENCE_DURATION_SEC` | 1.0 | 최소 침묵 길이 |
| `AD_BANNER_DURATION_SEC` | 10.0 | 배너 기본 표시 시간 |
| `RCNN_CONFIDENCE_THRESHOLD` | 0.5 | R-CNN bbox 신뢰도 필터 |
| `RCNN_BATCH_SIZE` | 200 | R-CNN 배치 크기 (메모리 제어) |
| `QWEN_SAMPLE_INTERVAL_SEC` | 60 | Qwen 샘플링 간격 (초) |
| `QWEN_MAX_SAMPLES` | 60 | 최대 Qwen 샘플 수 |
| `WHISPER_MODEL` | base | Whisper 모델 크기 |
| `API_HOST` | 0.0.0.0 | |
| `API_PORT` | 8000 | |
| `API_BASE_URL` | http://localhost:8000 | 프론트엔드에서 사용 |

---

### `backend/common/db.py`

psycopg2 래퍼. `RealDictCursor` 사용 (dict 반환).

| 함수 | 역할 |
|------|------|
| `get_connection()` | 새 DB 연결 생성 |
| `cursor(commit=True)` | context manager (자동 commit/rollback) |
| `execute(sql, params)` | INSERT/UPDATE/DELETE 실행 |
| `fetchone(sql, params)` | SELECT 첫 행 |
| `fetchall(sql, params)` | SELECT 모든 행 |

---

### `backend/common/rabbitmq.py`

pika 래퍼. 재연결 루프 내장.

| 함수 | 역할 |
|------|------|
| `publish(queue, payload)` | JSON 메시지 발행 |
| `consume(queue, callback, prefetch, ack_early)` | 블로킹 컨슈머 (자동 재연결) |

**재연결 전략**: 연결 끊김 시 5초 대기 후 재연결 반복
**heartbeat=7200**: 2시간 네트워크 유휴 허용

---

### `backend/common/logging_setup.py`

`TimedRotatingFileHandler` 기반 서비스별 독립 로그 파일.

- 로그 경로: `D:\20.WORKSPACE\2026_VOD_FAST_4\storage\logs\{service}.log`
- 형식: `%(asctime)s [%(levelname)s] %(name)s - %(message)s`
- 자정 자동 로테이션

---

## 6. 프론트엔드 (Next.js)

### 파일 구조

```
frontend/src/
  app/
    page.tsx                     홈페이지 (작업 제출 + 재생)
    layout.tsx                   루트 레이아웃
    player/[jobId]/page.tsx      플레이어 페이지 (상태 폴링)
    api/overlay/[jobId]/route.ts Next.js 프록시 (CORS 우회)
  components/
    VideoPlayer.tsx              비디오 플레이어 + 광고 오버레이
    AdOverlay.tsx                단일 광고 오버레이 컴포넌트
  types/
    overlay.ts                   TypeScript 인터페이스
```

### `page.tsx` — 홈페이지

**역할**: 영상 경로 입력 → 작업 제출 → 플레이어로 이동

```typescript
// 주요 상태
videoPath: string    // 서버 로컬 경로 (예: D:\video.mp4)
jobId: string        // 기존 작업 재생용

// API 호출
POST /jobs  Body: {"video_path": videoPath}
           Response: {"job_id": "uuid", "status": "pending"}
```

---

### `player/[jobId]/page.tsx` — 플레이어 페이지

**역할**: 작업 완료까지 5초 폴링 → VideoPlayer 렌더링

```typescript
// 페이지 상태 머신
type PageState =
  | { phase: "loading" }
  | { phase: "polling"; status: JobStatus }
  | { phase: "ready"; metadata: OverlayMetadata }
  | { phase: "error"; message: string }

// 폴링 로직
GET /api/overlay/{jobId}  → 성공: phase="ready"
                          → 실패: GET /jobs/{jobId} 조회
                               → status == complete: 재시도
                               → status == failed: 오류 표시
                               → 그 외: 5초 후 반복
```

---

### `api/overlay/[jobId]/route.ts` — 프록시

**역할**: Next.js 서버 → FastAPI 백엔드 프록시 (CORS 우회)

```typescript
GET /api/overlay/{jobId}
  → fetch(`${BACKEND_URL}/overlay/{jobId}`)
  → NextResponse.json(body)
```

---

### `components/VideoPlayer.tsx` — 비디오 플레이어

**역할**: HTML5 비디오 재생 + 오버레이 동적 렌더링 + 광고 타임라인 UI

| 함수 | 역할 |
|------|------|
| `handleTimeUpdate` | 현재 재생 위치 → 활성 오버레이 필터링 (4×/초) |
| `seekTo(seconds)` | 지정 시간으로 이동 |
| `togglePlay` | 재생/일시정지 전환 |
| `toggleMute` | 음소거 전환 |
| `activeOverlays` 계산 | `start <= currentTime < start+duration` 필터 |

**좌표 변환**
```typescript
// 백엔드: 원본 해상도 (예: 1920×1080)
// 프론트엔드: 실제 표시 해상도 (예: 1280×720)
scaleX = videoDisplayWidth  / videoNaturalWidth
scaleY = videoDisplayHeight / videoNaturalHeight

displayX = coordinates_x * scaleX
displayY = coordinates_y * scaleY
```

---

### `components/AdOverlay.tsx` — 광고 오버레이

**역할**: 단일 광고를 비디오 위에 절대 위치로 렌더링

| 광고 유형 | 렌더링 방식 | 특징 |
|---------|-----------|------|
| `video_clip` | `<video muted playsInline>` | 자동재생, 음소거 |
| `banner` | `<img objectFit="contain">` | 비율 유지 |

**CSS 특성**
```css
position: absolute     /* 비디오 위에 오버레이 */
opacity: 0.92          /* 약간 투명 */
borderRadius: 16px     /* 둥근 모서리 */
maxWidth: 28%          /* 화면 점유 제한 */
transition: opacity 0.35s  /* 페이드인 */
pointerEvents: none    /* 클릭 통과 */
zIndex: 10
```

---

### `types/overlay.ts` — TypeScript 인터페이스

```typescript
export interface OverlayEntry {
  matched_ad_id: string
  ad_resource_url: string
  ad_type: "video_clip" | "banner"
  overlay_start_time_sec: number
  overlay_duration_sec: number
  coordinates_x: number | null
  coordinates_y: number | null
  coordinates_w: number | null
  coordinates_h: number | null
  score: number
}

export interface OverlayMetadata {
  job_id: string
  original_video_url: string
  total_duration_sec: number
  overlays: OverlayEntry[]
}

export interface JobStatus {
  job_id: string
  status: string
  input_video_path: string
  error_message?: string | null
  created_at: string
  updated_at: string
}
```

---

## 7. 데이터 흐름 (End-to-End)

```
[브라우저]
  POST /jobs {"video_path": "D:\video.mp4"}
      |
[FastAPI: submit_job]
  INSERT job_history (status=pending)
  PUBLISH vod.step1.preprocess {"job_id": "uuid", "video_path": "..."}
      |
[RabbitMQ: vod.step1.preprocess]
      |
[Step 1: pipeline.py]
  UPDATE job_history → preprocessing
  ffmpeg → audio.wav, frame_*.jpg
  ffprobe → duration, fps, width, height
  INSERT video_preprocessing_info
  UPDATE job_history → analysing
  PUBLISH vod.step2.analysis {"job_id": "uuid"}
      |
[RabbitMQ: vod.step2.analysis]
      |
[Step 2: consumer.py]
  SELECT video_preprocessing_info → frame_dir, audio_path
  ┌─── Faster R-CNN (vision_rcnn.py) ───────────────────┐
  │  모든 프레임 분석 (200프레임 배치)                    │
  │  INSERT analysis_vision_context (safe_area, density)  │
  └──────────────────────────────────────────────────────┘
  ┌─── Qwen2-VL (vision_qwen.py) ──────────────────────┐
  │  샘플 프레임 (최대 60개) 분석                        │
  │  UPDATE analysis_vision_context (scene_description)  │
  └─────────────────────────────────────────────────────┘
  ┌─── librosa (audio_analysis.py) ────────────────────┐
  │  audio.wav → 침묵 구간 추출                          │
  │  INSERT analysis_audio                               │
  └─────────────────────────────────────────────────────┘
  ┌─── Whisper (audio_transcription.py) ───────────────┐
  │  audio.wav → 텍스트 세그먼트                         │
  │  INSERT analysis_transcript                          │
  └─────────────────────────────────────────────────────┘
  PUBLISH vod.step3.persistence {"job_id": "uuid"}
      |
[RabbitMQ: vod.step3.persistence]
      |
[Step 3: pipeline.py]
  SELECT analysis_audio → N개 침묵 구간
  SELECT ad_inventory → M개 광고
  N × M Cartesian product → candidates
  PUBLISH vod.step4.decision {"job_id": "uuid", "candidates": [...]}
      |
[RabbitMQ: vod.step4.decision]
      |
[Step 4: scoring.py]
  FOR EACH candidate:
    SELECT analysis_vision_context (가장 가까운 프레임)
    SELECT analysis_transcript (겹치는 자막)
    _compute_score() → 0~100+
  _pick_best_per_interval() → 침묵당 최고점 1개
  INSERT decision_result
  UPDATE job_history → complete
      |
[브라우저: 폴링]
  GET /api/overlay/{job_id}
      |
[FastAPI: get_overlay_metadata]
  SELECT job_history + video_preprocessing_info + decision_result
  → OverlayMetadata JSON
      |
[VideoPlayer + AdOverlay]
  <video> 재생
  timeupdate 4×/초 → activeOverlays 필터링
  → <AdOverlay> 절대 위치 렌더링
```

---

## 8. 파일 I/O 패턴

| 단계 | 작업 | 경로 | 형식 |
|------|------|------|------|
| Step 1 | 읽기 | 사용자 입력 경로 | .mp4 |
| Step 1 | 쓰기 | `storage/jobs/{job_id}/audio.wav` | WAV (16kHz mono PCM) |
| Step 1 | 쓰기 | `storage/jobs/{job_id}/frames/frame_*.jpg` | JPEG (1fps) |
| Step 2 | 읽기 | `storage/jobs/{job_id}/frames/frame_*.jpg` | JPEG (R-CNN, Qwen) |
| Step 2 | 읽기 | `storage/jobs/{job_id}/audio.wav` | WAV (Whisper, librosa) |
| API | 제공 | `storage/jobs/{job_id}/...` | 스트리밍 |
| API | 제공 | `..\2026_VOD_FAST_3\TV_CF\output\*.mp4` | 광고 영상 스트리밍 |
| API | 제공 | `..\2026_VOD_FAST_3\TV_CF\output_print\*.jpg` | 광고 이미지 스트리밍 |
| 로그 | 쓰기 | `storage/logs/{service}.log` | 텍스트 (자정 로테이션) |

---

## 9. 스코어링 로직

### 점수 항목

| 항목 | 점수 | 조건 | 데이터 소스 |
|------|------|------|------------|
| 침묵 충분 | +30 | `silence_duration >= ad_duration` | `analysis_audio` |
| 장면 전환 후 | +20 | 2초 내 `is_scene_cut=true` 프레임 존재 | `analysis_vision_context` |
| 빈 화면 | +20 | `object_density <= 0.3` | `analysis_vision_context` |
| 장면 키워드 | +10/개 | `scene_description`에 `target_mood` 키워드 포함 | `analysis_vision_context` |
| 자막 키워드 | +10/개 | `transcript_text`에 `target_mood` 키워드 포함 | `analysis_transcript` |
| 복잡한 화면 | -40 | `object_density >= 0.7` | `analysis_vision_context` |

### 선택 규칙

1. `score <= 0` 후보 제거
2. 동일 침묵 구간 내 최고점 광고 1개만 선택
3. 좌표: 해당 프레임의 `safe_area_x/y/w/h` 그대로 사용

### `target_mood` 키워드 예시 (Qwen2-VL 분석 결과)

- 운동 광고: `["sports", "fitness", "energy", "workout", "athletic"]`
- 식품 광고: `["food", "restaurant", "cooking", "meal", "fresh"]`
- 자동차 광고: `["car", "driving", "road", "speed", "luxury"]`
- 화장품 광고: `["beauty", "skincare", "cosmetics", "skin", "glow"]`

---

## 10. 함수 전체 목록

### Backend

| 파일 | 함수 | 역할 요약 |
|------|------|---------|
| `common/db.py` | `get_connection` | psycopg2 연결 |
| `common/db.py` | `cursor` | context manager |
| `common/db.py` | `execute` | INSERT/UPDATE/DELETE |
| `common/db.py` | `fetchone` | SELECT 첫 행 |
| `common/db.py` | `fetchall` | SELECT 모든 행 |
| `common/rabbitmq.py` | `publish` | 메시지 발행 |
| `common/rabbitmq.py` | `consume` | 블로킹 컨슈머 |
| `step1_preprocessing/pipeline.py` | `_job_storage_dir` | 저장소 경로 생성 |
| `step1_preprocessing/pipeline.py` | `_update_job_status` | job 상태 갱신 |
| `step1_preprocessing/pipeline.py` | `extract_audio` | ffmpeg WAV 추출 |
| `step1_preprocessing/pipeline.py` | `extract_frames` | ffmpeg JPEG 추출 |
| `step1_preprocessing/pipeline.py` | `get_video_metadata` | ffprobe 메타데이터 |
| `step1_preprocessing/pipeline.py` | `save_to_db` | preprocessing 정보 저장 |
| `step1_preprocessing/pipeline.py` | `run` | Step 1 실행 |
| `step1_preprocessing/pipeline.py` | `_on_message` | MQ 콜백 |
| `step2_analysis/vision_rcnn.py` | `_get_model` | R-CNN 모델 로드 |
| `step2_analysis/vision_rcnn.py` | `_largest_safe_rectangle` | 최대 빈 사각형 |
| `step2_analysis/vision_rcnn.py` | `_compute_safe_area` | safe_area + density |
| `step2_analysis/vision_rcnn.py` | `_is_scene_cut` | 장면 전환 감지 |
| `step2_analysis/vision_rcnn.py` | `analyse_frames` | 배치 프레임 분석 |
| `step2_analysis/vision_qwen.py` | `_get_model` | Qwen2-VL 로드 |
| `step2_analysis/vision_qwen.py` | `_describe_frame` | 프레임 → 장면 설명 |
| `step2_analysis/vision_qwen.py` | `_compute_sample_interval` | 적응형 샘플링 |
| `step2_analysis/vision_qwen.py` | `analyse_frames` | 샘플 프레임 분석 |
| `step2_analysis/audio_analysis.py` | `detect_silence` | 침묵 구간 추출 |
| `step2_analysis/audio_transcription.py` | `_load_model` | Whisper 로드 |
| `step2_analysis/audio_transcription.py` | `transcribe` | 음성 → 텍스트 |
| `step2_analysis/consumer.py` | `_insert_vision_batch` | R-CNN 배치 INSERT |
| `step2_analysis/consumer.py` | `_update_scene_descriptions` | Qwen 범위 UPDATE |
| `step2_analysis/consumer.py` | `_insert_audio_intervals` | 침묵 INSERT |
| `step2_analysis/consumer.py` | `_insert_transcript` | 자막 INSERT |
| `step2_analysis/consumer.py` | `_update_job_status` | job 상태 갱신 |
| `step2_analysis/consumer.py` | `_already_processed` | 멱등성 확인 |
| `step2_analysis/consumer.py` | `run` | Step 2 실행 |
| `step2_analysis/consumer.py` | `_on_message` | MQ 콜백 |
| `step3_persistence/pipeline.py` | `_update_job_status` | job 상태 갱신 |
| `step3_persistence/pipeline.py` | `_get_silence_intervals` | 침묵 구간 조회 |
| `step3_persistence/pipeline.py` | `_get_ad_inventory` | 광고 목록 조회 |
| `step3_persistence/pipeline.py` | `build_candidates` | 후보 쌍 생성 |
| `step3_persistence/pipeline.py` | `run` | Step 3 실행 |
| `step3_persistence/pipeline.py` | `_on_message` | MQ 콜백 |
| `step4_decision/scoring.py` | `_update_job_status` | job 상태 갱신 |
| `step4_decision/scoring.py` | `_get_vision_context_near` | 근접 프레임 조회 |
| `step4_decision/scoring.py` | `_get_transcript_text` | 구간 자막 합치기 |
| `step4_decision/scoring.py` | `_check_recent_cut` | 장면 전환 확인 |
| `step4_decision/scoring.py` | `_compute_score` | 스코어 계산 |
| `step4_decision/scoring.py` | `_pick_best_per_interval` | 최고점 1개 선택 |
| `step4_decision/scoring.py` | `_insert_decision_results` | 결정 저장 |
| `step4_decision/scoring.py` | `run` | Step 4 실행 |
| `step4_decision/scoring.py` | `_on_message` | MQ 콜백 |
| `step5_api/server.py` | `_ad_url` | 광고 URL 생성 |
| `step5_api/server.py` | `submit_job` | POST /jobs 핸들러 |
| `step5_api/server.py` | `get_job_status` | GET /jobs/{id} 핸들러 |
| `step5_api/server.py` | `get_overlay_metadata` | GET /overlay/{id} 핸들러 |
| `step5_api/server.py` | `serve_source_video` | 원본 영상 서빙 |

### Frontend

| 파일 | 함수/컴포넌트 | 역할 |
|------|------------|------|
| `app/page.tsx` | `HomePage` | 작업 제출 페이지 |
| `app/page.tsx` | `handleSubmit` | POST /jobs 호출 |
| `app/page.tsx` | `handleWatch` | 플레이어로 이동 |
| `app/player/[jobId]/page.tsx` | `PlayerPage` | 플레이어 페이지 + 폴링 |
| `app/player/[jobId]/page.tsx` | `fetchOverlay` | 오버레이 데이터 조회 |
| `app/player/[jobId]/page.tsx` | `fetchStatus` | 작업 상태 조회 |
| `app/player/[jobId]/page.tsx` | `StatusBadge` | 상태 뱃지 컴포넌트 |
| `app/api/overlay/[jobId]/route.ts` | `GET` | FastAPI 프록시 |
| `components/VideoPlayer.tsx` | `VideoPlayer` | 플레이어 + 오버레이 |
| `components/VideoPlayer.tsx` | `handleTimeUpdate` | 재생 위치 추적 |
| `components/VideoPlayer.tsx` | `seekTo` | 시크 헬퍼 |
| `components/VideoPlayer.tsx` | `togglePlay` | 재생/일시정지 |
| `components/VideoPlayer.tsx` | `toggleMute` | 음소거 전환 |
| `components/AdOverlay.tsx` | `AdOverlay` | 단일 광고 오버레이 |

---

*마지막 갱신: 2026-03-12*
