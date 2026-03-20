# VOD Ad Overlay System — 파이프라인 전체 문서 v2 (EXAMPLE)

> **[SECURITY NOTE]** 이 파일은 공개용 예시 파일입니다.
> 실제 운영 환경 정보(IP, 포트, 계정, 비밀번호, 경로)는 모두 placeholder로 대체되어 있습니다.
> 실제 설정은 `PIPELINE_v2.md` (gitignore에 등록됨)에서 관리하세요.

> **2026_VOD_FAST_4** | 비디오 문맥 분석 기반 동적 광고 오버레이 시스템
> 현재 버전: **v2.13 (Step2 분리 + Step4 쿼리 최적화)**

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
10. [VLM 백엔드 선택](#10-vlm-백엔드-선택)
11. [큐 수동 재투입 명령어](#11-큐-수동-재투입-명령어)
12. [DB 초기화 쿼리](#12-db-초기화-쿼리)
13. [함수 전체 목록](#13-함수-전체-목록)

---

## 1. 시스템 개요

4단계 AI 분석 파이프라인이 RabbitMQ를 통해 비동기로 연결되며, PostgreSQL에 데이터를 축적한 뒤 Next.js 프론트엔드에서 광고를 실시간 오버레이한다.

### 전체 파이프라인 흐름

```
[사전 준비] analyze_ad_narrative_gemini → ad_inventory.target_narrative, ad_category 채움
        |
  Step 1: Preprocessing    ffmpeg 프레임/오디오 추출 + scenedetect 시각적 컷
        |
  Step 2-A: Vision         YOLOv8l 객체 탐지 + VLM 고정샘플링 (컨테이너: step2-a)
  Step 2-B: Audio          침묵 감지 + Whisper STT (컨테이너: step2-b)
  Step 2-C: Phase A        MiniLM 임베딩 씬 분절 + VLM narrative (컨테이너: step2-c)
                           ※ 2-A, 2-B 병렬 실행 → DB 플래그 게이트 → 2-C 실행
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
| 객체 감지 | YOLOv8l (ultralytics) |
| 장면 이해 (Step2) | Qwen2-VL-2B-Instruct **또는** Gemini (VLM_BACKEND 환경변수) |
| 광고 분석 | Gemini Flash (google-genai SDK) |
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

### 이미지 6개 / 서비스 8개 (v2.13)

| 이미지 | 빌드 대상 | 사용 서비스 |
|--------|----------|-----------|
| `vod-backend:latest` | `Dockerfile.backend` | step1, step3, step4, step5-api |
| `vod-step2a:latest` | `Dockerfile.step2a` | step2-a (YOLO + VLM) |
| `vod-step2b:latest` | `Dockerfile.step2b` | step2-b (침묵 + Whisper) |
| `vod-step2c:latest` | `Dockerfile.step2c` | step2-c (Phase A gate) |
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

# Step 1 (step2a/b 동시 발행 + 플래그 리셋)
scp backend/step1_preprocessing/pipeline.py <SSH_USER>@<SERVER_IP>:/path/to/pipeline/step1_preprocessing/pipeline.py

# Step 2 분리 컨테이너 소스
scp backend/step2_analysis/consumer_a.py         <SSH_USER>@<SERVER_IP>:/path/to/pipeline/step2_analysis/consumer_a.py
scp backend/step2_analysis/consumer_b.py         <SSH_USER>@<SERVER_IP>:/path/to/pipeline/step2_analysis/consumer_b.py
scp backend/step2_analysis/consumer_c.py         <SSH_USER>@<SERVER_IP>:/path/to/pipeline/step2_analysis/consumer_c.py

# Step 2 Dockerfile + requirements
scp backend/Dockerfile.step2a              <SSH_USER>@<SERVER_IP>:/path/to/pipeline/Dockerfile.step2a
scp backend/Dockerfile.step2b              <SSH_USER>@<SERVER_IP>:/path/to/pipeline/Dockerfile.step2b
scp backend/Dockerfile.step2c              <SSH_USER>@<SERVER_IP>:/path/to/pipeline/Dockerfile.step2c
scp backend/requirements.step2a.txt       <SSH_USER>@<SERVER_IP>:/path/to/pipeline/requirements.step2a.txt
scp backend/requirements.step2b.txt       <SSH_USER>@<SERVER_IP>:/path/to/pipeline/requirements.step2b.txt
scp backend/requirements.step2c.txt       <SSH_USER>@<SERVER_IP>:/path/to/pipeline/requirements.step2c.txt

# Step 4 (prefetch 최적화 + lazy import)
scp backend/step4_decision/scoring.py    <SSH_USER>@<SERVER_IP>:/path/to/pipeline/step4_decision/scoring.py

# analyze-narrative 서비스
scp backend/analyze_ad_narrative_gemini.py <SSH_USER>@<SERVER_IP>:/path/to/analyze-narrative/analyze_ad_narrative_gemini.py
```

### 4.2 Docker 이미지 빌드

```bash
# step2-a/b/c만 변경된 경우
docker-compose -f docker-compose.pipeline.yml build step2-a step2-b step2-c
docker-compose -f docker-compose.pipeline.yml up -d --force-recreate step2-a step2-b step2-c

# step1/step4 (vod-backend) 변경된 경우
docker-compose -f docker-compose.pipeline.yml build step1
docker-compose -f docker-compose.pipeline.yml up -d --force-recreate step1 step3 step4 step5-api

# 전체 빌드
docker-compose -f docker-compose.pipeline.yml build
docker-compose -f docker-compose.pipeline.yml up -d --force-recreate
```

### 4.3 서비스 로그 확인

```bash
# Step 2 개별 컨테이너
docker logs -f pipeline-step2-a-1
docker logs -f pipeline-step2-b-1
docker logs -f pipeline-step2-c-1

# Step 4 / API
docker logs -f pipeline-step4-1
docker logs -f pipeline-step5-api-1
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
| `ad_placement_feedback` | 사용자 피드백 (-1/0/1) |

---

## 6. RabbitMQ 큐 구조

| 큐 이름 | 생산자 | 소비자 |
|---------|--------|--------|
| `vod.{prefix}.step1.preprocess` | FastAPI POST /jobs | Step 1 |
| `vod.{prefix}.step2a.vision` | Step 1 (fan-out) | Step 2-A (YOLO + VLM) |
| `vod.{prefix}.step2b.audio` | Step 1 (fan-out) | Step 2-B (침묵 + Whisper) |
| `vod.{prefix}.step2.gate` | Step 2-A, Step 2-B | Step 2-C (Phase A gate) |
| `vod.{prefix}.step3.persist` | Step 2-C | Step 3 |
| `vod.{prefix}.step4.decide` | Step 3 | Step 4 |

> Step 1은 QUEUE_STEP2A + QUEUE_STEP2B에 **동시 발행** (fan-out).
> Step 2-A, Step 2-B는 각자 완료 후 step2a_done / step2b_done 플래그를 TRUE로 설정하고 QUEUE_STEP2_GATE에 발행.
> Step 2-C는 두 플래그가 모두 TRUE일 때 Phase A 실행 (30초 폴링, 최대 30분 대기).

---

## 7. 파이프라인 단계별 상세

| Step | 파일 | 이미지 |
|------|------|--------|
| Step 1 | `step1_preprocessing/pipeline.py` | vod-backend |
| Step 2-A | `step2_analysis/consumer_a.py` | vod-step2a |
| Step 2-B | `step2_analysis/consumer_b.py` | vod-step2b |
| Step 2-C | `step2_analysis/consumer_c.py` | vod-step2c |
| Step 3 | `step3_persistence/pipeline.py` | vod-backend |
| Step 4 | `step4_decision/scoring.py` | vod-backend |
| Step 5 | `step5_api/server.py` | vod-backend |

### Step 2 씬 세그멘테이션 (v2.11)

`dialogue_segmenter.py` 주요 상수:

| 상수 | 값 | 설명 |
|------|-----|------|
| `BOUNDARY_THRESHOLD` | **0.75** | 씬 경계 판단 유사도 임계값 (v2.11 상향: 0.52→0.75) |
| `MIN_WINDOW_SEC` | 30.0 | 씬 최소 길이(초) |
| `MAX_WINDOW_SEC` | 240.0 | 씬 최대 길이(초) |
| `CHUNK_DURATION_SEC` | 15.0 | 임베딩 청크 단위(초) |

> **v2.11 버그 수정**: 짧은 씬 병합 로직에서 cascade 버그 수정.
> 기존 로직은 짧은 씬이 연속될 때 모두 앞으로 합쳐져 전체 영상이 씬 1개로 병합되는 문제가 있었음.
> 수정: 누적 씬이 `MIN_WINDOW_SEC` 충족 시 새 씬을 시작하도록 변경.

### Step 3→Step 4 메시지 경량화 (v2.12)

| 항목 | 변경 전 (v2.11) | 변경 후 (v2.12) |
|------|----------------|----------------|
| Step3 발행 페이로드 | `{"job_id": ..., "candidates": [...97,161개...]}` (~140MB) | `{"job_id": ...}` (~수십 바이트) |
| Step4 candidates 출처 | RabbitMQ 메시지 | DB 직접 조회 (`build_candidates(job_id)`) |
| 문제 | consumer_timeout → Step4 미실행 | 해소 |

---

## 8. 스코어링 로직 (v2.12)

### 필터

| 조건 | 처리 |
|------|------|
| context_narrative ↔ target_narrative 유사도 < 0.30 | Skip |
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

### Gemini 버전 (권장)

```bash
cd /path/to/Docker/analyze-narrative

# 미처리 광고만 분석
docker-compose -f docker-compose.analyze-narrative.yml run --rm analyze-narrative

# 전체 재분석
docker-compose -f docker-compose.analyze-narrative.yml run --rm analyze-narrative --force

# 테스트 (N개)
docker-compose -f docker-compose.analyze-narrative.yml run --rm analyze-narrative --limit 10
```

### Gemini 무료 티어 제한 (gemini-3-flash-preview 기준)

| 항목 | 한도 | 비고 |
|------|------|------|
| RPM | 5 | 분당 최대 5 요청 |
| RPD | 20 | **일일 최대 20 요청** — 핵심 병목 |
| TPM | 250K | 토큰은 여유 있음 |

> RPD=20 → 699개 광고 기준 약 35일 소요.
> 매일 cron으로 자동 재실행하거나 유료 플랜 전환 권장.
> 코드에서 NULL 저장된 광고는 다음 실행 시 자동 재처리됨.

### 환경변수 (.env)

```env
GEMINI_API_KEY=<YOUR_GEMINI_API_KEY>
GEMINI_MODEL=gemini-3-flash-preview
```

---

## 10. VLM 백엔드 선택

Step 2의 VLM을 환경변수로 동적 전환 가능.

| VLM_BACKEND | 모델 | 특징 |
|-------------|------|------|
| `qwen` (기본값) | Qwen2-VL-2B-Instruct | 로컬 실행, API 제한 없음, CPU에서 느림 |
| `gemini` | Gemini Flash | 빠름, 무료 15 RPM 제한 → 파이프라인 블로킹 위험 |

> **권장**: Step 2는 `qwen`, 광고 분석(`analyze-narrative`)은 `gemini`
> - Step 2는 새 VOD마다 반복 실행 → Gemini 429 발생 시 파이프라인 전체 블로킹
> - 광고 분석은 일회성 배치 → 429 재시도 허용

### .env 설정

```env
VLM_BACKEND=qwen
GEMINI_API_KEY=<YOUR_GEMINI_API_KEY>
GEMINI_MODEL=gemini-3-flash-preview
```

---

## 11. 큐 수동 재투입 명령어

> **형식 규칙**: `docker exec` 방식 사용. SSH 원격 명령어 방식 사용 금지.

### Step 2 재처리 (Step 2-A + 2-B 동시 fan-out)

```bash
docker exec pipeline-step5-api-1 python3 -c "
import sys
sys.path.insert(0, '/app')
from common import rabbitmq as mq, config, db
JOB_ID = '<JOB_ID>'
db.execute('UPDATE job_history SET step2a_done=FALSE, step2b_done=FALSE WHERE job_id=%s', (JOB_ID,))
mq.publish(config.QUEUE_STEP2A, {'job_id': JOB_ID})
mq.publish(config.QUEUE_STEP2B, {'job_id': JOB_ID})
print('Published to', config.QUEUE_STEP2A, 'and', config.QUEUE_STEP2B)
"
```

### Step 3 재처리

```bash
docker exec pipeline-step5-api-1 python3 -c "
import sys
sys.path.insert(0, '/app')
from common import rabbitmq as mq, config
mq.publish(config.QUEUE_STEP3, {'job_id': '<JOB_ID>'})
print('Published to', config.QUEUE_STEP3)
"
```

### Step 4 재처리

```bash
docker exec pipeline-step5-api-1 python3 -c "
import sys
sys.path.insert(0, '/app')
from common import rabbitmq as mq, config
mq.publish(config.QUEUE_STEP4, {'job_id': '<JOB_ID>'})
print('Published to', config.QUEUE_STEP4)
"
```

### 특정 Job DB 초기화 후 Step 2부터 재처리

```bash
docker exec pipeline-step5-api-1 python3 -c "
import sys
sys.path.insert(0, '/app')
from common import db, rabbitmq as mq, config
JOB_ID = '<JOB_ID>'
db.execute(\"DELETE FROM decision_result WHERE job_id = %s\", (JOB_ID,))
db.execute(\"DELETE FROM analysis_scene WHERE job_id = %s\", (JOB_ID,))
db.execute(\"DELETE FROM analysis_audio WHERE job_id = %s\", (JOB_ID,))
db.execute(\"DELETE FROM analysis_transcript WHERE job_id = %s\", (JOB_ID,))
db.execute(\"DELETE FROM analysis_vision_context WHERE job_id = %s\", (JOB_ID,))
db.execute(\"UPDATE job_history SET status = 'preprocessing_done', step2a_done=FALSE, step2b_done=FALSE WHERE job_id = %s\", (JOB_ID,))
mq.publish(config.QUEUE_STEP2A, {'job_id': JOB_ID})
mq.publish(config.QUEUE_STEP2B, {'job_id': JOB_ID})
print('Cleared and published to', config.QUEUE_STEP2A, 'and', config.QUEUE_STEP2B)
"
```

---

## 12. DB 초기화 쿼리

### 전체 Job 삭제 (ad_inventory 제외)

외래키 제약으로 인해 순서 중요.

```sql
TRUNCATE TABLE ad_placement_feedback CASCADE;
TRUNCATE TABLE decision_result CASCADE;
TRUNCATE TABLE analysis_scene CASCADE;
TRUNCATE TABLE analysis_audio CASCADE;
TRUNCATE TABLE analysis_transcript CASCADE;
TRUNCATE TABLE analysis_vision_context CASCADE;
TRUNCATE TABLE video_preprocessing_info CASCADE;
TRUNCATE TABLE job_history CASCADE;
```

### 진단 쿼리

```sql
-- target_narrative 채움 현황
SELECT
    COUNT(*) AS 전체,
    COUNT(target_narrative) AS narrative있음,
    COUNT(*) - COUNT(target_narrative) AS narrative없음
FROM ad_inventory;

-- 최신 Job 씬/대사 수 확인
SELECT
    j.job_id,
    v.duration_sec,
    COUNT(t.id) AS 대사수
FROM job_history j
JOIN video_preprocessing_info v ON j.job_id = v.job_id
LEFT JOIN analysis_transcript t ON j.job_id = t.job_id
WHERE j.job_id = (SELECT job_id FROM job_history ORDER BY created_at DESC LIMIT 1)
GROUP BY j.job_id, v.duration_sec;

-- decision_result 유사도 현황
SELECT
    COUNT(*) AS 최종매칭건수,
    ROUND(AVG(similarity_score)::numeric, 3) AS 평균유사도,
    ROUND(MIN(similarity_score)::numeric, 3) AS 최소유사도,
    ROUND(MAX(similarity_score)::numeric, 3) AS 최대유사도,
    ROUND(AVG(score)::numeric, 0) AS 평균점수
FROM decision_result;
```

---

## 13. 함수 전체 목록

| 모듈 | 주요 함수 |
|------|---------|
| `step1_preprocessing/pipeline.py` | `extract_audio`, `extract_frames`, `detect_scene_cuts`, `run` |
| `step2_analysis/vision_yolo.py` | `analyse_frames`, `_compute_safe_area` |
| `step2_analysis/dialogue_segmenter.py` | `segment_video`, `find_context_start` |
| `step2_analysis/vision_qwen.py` | `analyse_scene_context`, `analyse_frames` |
| `step2_analysis/vision_gemini.py` | `analyse_scene_context`, `analyse_frames` |
| `step2_analysis/consumer_a.py` | `run` (YOLO + VLM 고정샘플링, step2a_done 플래그) |
| `step2_analysis/consumer_b.py` | `run` (침묵감지 + Whisper STT, step2b_done 플래그) |
| `step2_analysis/consumer_c.py` | `_wait_for_gate`, `_generate_scene_contexts`, `run` |
| `step3_persistence/pipeline.py` | `build_candidates`, `run` |
| `step4_decision/embedding_scorer.py` | `score_narrative_fit`, `batch_similarity_matrix`, `compute_similarity` |
| `step4_decision/scoring.py` | `_compute_score`, `_find_best_overlay_window`, `_get_scene_frames_cached`, `_get_silence_overlap_cached`, `run` |
| `step5_api/server.py` | `submit_job`, `get_overlay_metadata`, `serve_source_video` |
| `analyze_ad_narrative.py` | `_build_prompt`, `_analyse_ad`, `run` (Qwen 버전) |
| `analyze_ad_narrative_gemini.py` | `_call_gemini`, `_analyse_ad`, `run` (Gemini 버전) |
