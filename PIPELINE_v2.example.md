# VOD Ad Overlay System — 파이프라인 전체 문서 v2 (EXAMPLE)

> **[SECURITY NOTE]** 이 파일은 공개용 예시 파일입니다.
> 실제 운영 환경 정보(IP, 포트, 계정, 비밀번호, 경로)는 모두 placeholder로 대체되어 있습니다.
> 실제 설정은 `PIPELINE_v2.md` (gitignore에 등록됨)에서 관리하세요.

> **2026_VOD_FAST_4** | 비디오 문맥 분석 기반 동적 광고 오버레이 시스템
> 현재 버전: **v2.10 (Ad Category Matching)**

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [인프라 연결 정보](#2-인프라-연결-정보)
3. [Docker 배포 구조](#3-docker-배포-구조)
4. [서버 전송 → 빌드 → 실행 명령어](#4-서버-전송--빌드--실행-명령어)
5. [데이터베이스 스키마](#5-데이터베이스-스키마)
6. [RabbitMQ 큐 구조](#6-rabbitmq-큐-구조)
7. [파이프라인 단계별 상세](#7-파이프라인-단계별-상세)
8. [스코어링 로직](#8-스코어링-로직)
9. [광고 분석 서비스 (analyze-narrative)](#9-광고-분석-서비스-analyze-narrative)
10. [함수 전체 목록](#10-함수-전체-목록)

---

## 1. 시스템 개요

4단계 AI 분석 파이프라인이 RabbitMQ를 통해 비동기로 연결되며, PostgreSQL에 데이터를 축적한 뒤 Next.js 프론트엔드에서 광고를 실시간 오버레이한다.

### 전체 파이프라인 흐름

```
[사전 준비] analyze_ad_narrative → ad_inventory.target_narrative, ad_category 채움
        |
  Step 1: Preprocessing    ffmpeg 프레임/오디오 추출 + scenedetect 시각적 컷
        |
  Step 2: Analysis         YOLOv8l + Qwen2-VL + Whisper + librosa
                           Phase A: 씬 세그멘테이션 → analysis_scene
                           Phase B: 침묵 감지 + YOLO safe area
        |
  Step 3: Candidates       analysis_scene × ad_inventory Cartesian product
        |
  Step 4: Scoring          narrative 유사도 + 카테고리 보너스 + 슬라이딩 윈도우
        |
  Step 5: API (FastAPI)    오버레이 메타데이터 + 미디어 스트리밍
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
| STT | OpenAI Whisper small (Docker) |
| 음성 분석 | librosa |
| 씬 감지 | scenedetect>=0.6.4 |
| 컨테이너 | Docker Compose v1 |

---

## 2. 인프라 연결 정보

> **주의**: 아래 값들은 placeholder입니다. 실제 값은 `PIPELINE_v2.md` 참조.

| 항목 | 예시값 (실제 값 아님) |
|------|--------------------|
| PostgreSQL | `<YOUR_SERVER_IP>:5432` DB=`<DB_NAME>` user=`<DB_USER>` pw=`<DB_PASSWORD>` |
| RabbitMQ | `<YOUR_SERVER_IP>:5672` user=`<MQ_USER>` pw=`<MQ_PASSWORD>` |
| 광고 영상 (호스트) | `/path/to/ad_assets/video/` |
| 광고 이미지 (호스트) | `/path/to/ad_assets/image/` |
| VOD 영상 (호스트) | `/path/to/vod/` |
| 스토리지 (호스트) | `/path/to/storage/` |
| API 외부 접근 URL | `http://<YOUR_SERVER_IP>:8000` |
| 프론트엔드 URL | `http://<YOUR_SERVER_IP>:3000` |

### 서버 배포 경로 (예시)

| 역할 | 경로 |
|------|------|
| 파이프라인 소스 | `/path/to/Docker/pipeline/` |
| 프론트엔드 소스 | `/path/to/Docker/frontend/` |
| analyze-narrative | `/path/to/Docker/analyze-narrative/` |

### SSH 접속

```bash
ssh <SSH_USER>@<YOUR_SERVER_IP>
```

---

## 3. Docker 배포 구조

### 이미지 3개 / 서비스 6개

| 이미지 | 빌드 대상 | 사용 서비스 |
|--------|----------|-----------|
| `vod-backend:latest` | `Dockerfile.backend` | step1, step3, step4, step5-api |
| `vod-step2:latest` | `Dockerfile.step2` | step2 |
| `vod-frontend:latest` | `Dockerfile.frontend` | frontend |

### 볼륨 마운트

| 호스트 경로 | 컨테이너 경로 |
|------------|-------------|
| `/path/to/vod` | `/vod:ro` |
| `/path/to/storage` | `/app/storage` |
| `/path/to/ad_assets/video` | `/ads/video:ro` |
| `/path/to/ad_assets/image` | `/ads/banner:ro` |

---

## 4. 서버 전송 → 빌드 → 실행 명령어

### 4.1 소스 파일 서버 전송 (SCP)

```bash
# 공통 모듈
scp backend/common/config.py     <SSH_USER>@<SERVER_IP>:/path/to/pipeline/common/config.py
scp backend/common/db.py         <SSH_USER>@<SERVER_IP>:/path/to/pipeline/common/db.py
scp backend/common/rabbitmq.py   <SSH_USER>@<SERVER_IP>:/path/to/pipeline/common/rabbitmq.py

# Step 1~5 파일도 동일한 패턴으로 전송
scp backend/step1_preprocessing/pipeline.py <SSH_USER>@<SERVER_IP>:/path/to/pipeline/step1_preprocessing/pipeline.py
# ... (나머지 파일들)
```

### 4.2 Docker 이미지 빌드

```bash
ssh <SSH_USER>@<SERVER_IP>
cd /path/to/Docker/pipeline

# 빌드
docker-compose -f docker-compose.pipeline.yml build

# 전체 서비스 기동
docker-compose -f docker-compose.pipeline.yml up -d

# 상태 확인
docker-compose -f docker-compose.pipeline.yml ps
```

### 4.3 서비스 로그 확인

```bash
docker-compose -f docker-compose.pipeline.yml logs -f step2
docker-compose -f docker-compose.pipeline.yml logs -f step5-api
```

### 4.4 DB 초기화 (최초 1회)

```bash
docker-compose -f docker-compose.pipeline.yml run --rm step5-api python init_db.py
docker-compose -f docker-compose.pipeline.yml run --rm step5-api python populate_ad_inventory.py
```

---

## 5. 데이터베이스 스키마

### 테이블 목록

| 테이블 | 역할 |
|--------|------|
| `job_history` | 작업 메타데이터 및 상태 추적 |
| `video_preprocessing_info` | Step 1 추출 결과 (scene_cut_times JSONB) |
| `analysis_vision_context` | YOLOv8l + Qwen2-VL 프레임 분석 |
| `analysis_audio` | 음성 침묵 구간 |
| `analysis_transcript` | Whisper STT 자막 |
| `analysis_scene` | 씬 세그멘테이션 결과 + context_narrative |
| `ad_inventory` | 광고 자산 카탈로그 (target_narrative, ad_category, ad_category_path) |
| `decision_result` | 최종 광고 삽입 결정 (score, similarity_score, scene_duration_sec, avg_density) |

---

## 6. RabbitMQ 큐 구조

| 큐 이름 | 생산자 | 소비자 |
|---------|--------|--------|
| `vod.step1.preprocess` | FastAPI POST /jobs | Step 1 |
| `vod.step2.analyze` | Step 1 | Step 2 |
| `vod.step3.persist` | Step 2 | Step 3 |
| `vod.step4.decide` | Step 3 | Step 4 |

---

## 7. 파이프라인 단계별 상세

Step별 상세 내용은 각 소스 파일 docstring 및 README.md 참조.

| Step | 파일 | 이미지 |
|------|------|--------|
| Step 1 | `step1_preprocessing/pipeline.py` | vod-backend |
| Step 2 | `step2_analysis/consumer.py` | vod-step2 |
| Step 3 | `step3_persistence/pipeline.py` | vod-backend |
| Step 4 | `step4_decision/scoring.py` | vod-backend |
| Step 5 | `step5_api/server.py` | vod-backend |

---

## 8. 스코어링 로직 (v2.10)

### 필터

| 조건 | 처리 |
|------|------|
| context_narrative ↔ target_narrative 유사도 < 0.50 | Skip |
| video_clip: 씬 길이 < 광고 길이 | Skip |
| 총점 < 20 | 광고 없음 판정 |

### 점수 항목

| 항목 | 점수 | 조건 |
|------|------|------|
| narrative 유사도 스케일링 | 0~+80 | 0.40~1.0 → 0~80 |
| 빈 화면 | +20 | avg_density ≤ 0.3 |
| 침묵 가점 | +15 | 침묵 구간 겹침 |
| 카테고리 매칭 보너스 | +10 | ad_category ↔ context_narrative 유사도 ≥ 0.35 |
| 복잡한 화면 | -40 | avg_density ≥ 0.7 |

---

## 9. 광고 분석 서비스 (analyze-narrative)

```bash
# 배포 경로 (예시)
cd /path/to/Docker/analyze-narrative

# 미처리 광고만 분석
docker-compose -f docker-compose.analyze-narrative.yml run --rm analyze-narrative

# 전체 재분석
docker-compose -f docker-compose.analyze-narrative.yml run --rm analyze-narrative --force

# 테스트 (N개)
docker-compose -f docker-compose.analyze-narrative.yml run --rm analyze-narrative --limit 10
```

---

## 10. 함수 전체 목록

실제 함수 목록은 `PIPELINE_v2.md` (비공개) 또는 각 소스 파일 참조.

| 모듈 | 주요 함수 |
|------|---------|
| `step1_preprocessing/pipeline.py` | `extract_audio`, `extract_frames`, `detect_scene_cuts`, `run` |
| `step2_analysis/vision_yolo.py` | `analyse_frames`, `_compute_safe_area` |
| `step2_analysis/dialogue_segmenter.py` | `segment_video`, `find_context_start` |
| `step2_analysis/vision_qwen.py` | `analyse_scene_context`, `analyse_frames` |
| `step2_analysis/consumer.py` | `_generate_scene_contexts`, `run` |
| `step3_persistence/pipeline.py` | `build_candidates`, `run` |
| `step4_decision/embedding_scorer.py` | `score_narrative_fit`, `batch_similarity_matrix`, `compute_similarity` |
| `step4_decision/scoring.py` | `_compute_score`, `_find_best_overlay_window`, `run` |
| `step5_api/server.py` | `submit_job`, `get_overlay_metadata`, `serve_source_video` |
| `analyze_ad_narrative.py` | `_build_prompt`, `_analyse_ad`, `run` |
