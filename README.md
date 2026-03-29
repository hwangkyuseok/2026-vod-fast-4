# VOD Dynamic Ad Overlay System

비디오 문맥 분석 기반 동적 광고 오버레이 시스템

> **현재 버전: v2.15** | 음성 우선 분석 알고리즘 (Step2 A=오디오, B=비전)

> **[SECURITY NOTE]** 이 파일은 공개용 예시 파일입니다.
> 실제 운영 환경 정보(IP, 포트, 계정, 비밀번호, 경로)는 모두 placeholder로 대체되어 있습니다.
> 실제 설정은 `README.md` (gitignore에 등록됨)에서 관리하세요.

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
│   ├── step1_preprocessing/       # ffmpeg 추출, scenedetect
│   ├── step2_analysis/
│   │   ├── consumer_a.py          # Step2-A: 오디오 우선 (Whisper+SBERT)
│   │   ├── consumer_b.py          # Step2-B: 비전 후속 (YOLO+Gemini)
│   │   ├── audio_analysis.py      # librosa 묵음 구간 감지
│   │   ├── vision_yolo.py         # YOLOv8l safe area 분석
│   │   ├── vision_gemini.py       # Gemini 씬 서술 (상황/감정/욕구)
│   │   └── vision_qwen.py         # Qwen2-VL 씬 서술 (fallback)
│   ├── step3_persistence/         # 씬 × 광고 후보 페어 생성
│   ├── step4_decision/            # 스코어링 → decision_result 저장
│   │   ├── decision.py            # 메인 스코어링 파이프라인
│   │   ├── pre_filter.py          # MiniLM pre-filter
│   │   ├── cross_encoder_scorer.py
│   │   └── embedding_scorer.py
│   ├── step5_api/                 # FastAPI REST 서버
│   ├── init_schema.sql            # DB DDL
│   ├── init_db.py                 # DB 초기화
│   └── populate_ad_inventory.py   # 로컬 광고 파일 등록
├── frontend/                      # Next.js 14 App
├── docker-compose.pipeline.yml    # 서버 Docker 파이프라인
├── README.md                      # 로컬 전용 (gitignore) — 실제 접속정보 포함
├── README.example.md              # GitHub 공개용 — placeholder
├── COMMANDS.md                    # 로컬 전용 (gitignore) — 실제 명령어
├── PIPELINE_v2.md                 # 로컬 전용 (gitignore) — 실제 접속정보 포함
├── PIPELINE_v2.example.md         # GitHub 공개용 — placeholder
├── start.ps1 / start.bat          # 로컬 서비스 시작
├── stop.ps1  / stop.bat           # 로컬 서비스 중지
└── status.ps1 / status.bat        # 로컬 서비스 상태 확인
```

---

## 로컬 실행 가이드

### 1. 사전 준비

```bash
python -m venv .venv
.venv\Scripts\activate

pip install -r backend/requirements.step2a.txt
pip install -r backend/requirements.step2b.txt
# ... 각 스텝별 requirements 설치
```

### 2. 환경변수 설정

`.env` 파일을 생성하고 실제 값 입력 (README.md 참고):

```env
DB_HOST=<YOUR_DB_HOST>
DB_PORT=<YOUR_DB_PORT>
DB_NAME=<YOUR_DB_NAME>
DB_USER=<YOUR_DB_USER>
DB_PASSWORD=<YOUR_DB_PASSWORD>
RABBITMQ_HOST=<YOUR_RABBITMQ_HOST>
RABBITMQ_USER=<YOUR_RABBITMQ_USER>
RABBITMQ_PASSWORD=<YOUR_RABBITMQ_PASSWORD>
GEMINI_API_KEY=<YOUR_GEMINI_API_KEY>
```

### 3. DB 스키마 초기화

```bash
cd backend
python init_db.py
```

### 4. 광고 인벤토리 등록

```bash
python populate_ad_inventory.py
python analyze_ad_narrative.py
```

### 5. 서비스 시작 (Windows — 권장)

```powershell
.\start.ps1               # 전체 시작
.\start.ps1 -SkipFrontend # Frontend 제외
.\status.ps1              # 상태 확인
.\stop.ps1                # 전체 중지
```

### 6. 수동 시작 (터미널 7개)

```bash
cd backend && python -m step1_preprocessing.pipeline --consume
cd backend && python -m step2_analysis.consumer_a
cd backend && python -m step2_analysis.consumer_b
cd backend && python -m step3_persistence.pipeline
cd backend && python -m step4_decision.decision
cd backend && python -m step5_api.server
cd frontend && npm run dev
```

### 7. 작업 제출

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"video_path": "C:\\path\\to\\video.mp4"}'
```

---

## DB 테이블 구조

| 테이블 | 설명 |
|--------|------|
| `job_history` | 작업 이력 및 상태 관리 |
| `video_preprocessing_info` | 전처리 메타데이터 |
| `analysis_audio` | librosa 묵음 구간 |
| `analysis_transcript` | faster-whisper STT 세그먼트 |
| `analysis_scene` | SBERT 씬 분절 결과 (situation, emotion, desire) |
| `analysis_vision_context` | 프레임별 YOLO 분석 결과 |
| `ad_inventory` | 광고 소재 목록 |
| `decision_result` | 최종 광고 삽입 결정 |

---

## 스코어링 기준 (v2.14)

| 조건 | 점수 |
|------|------|
| narrative 유사도 스케일링 | 0 ~ +80 |
| 객체 밀집도 ≤ 0.3 | +20 |
| 침묵 구간 겹침 | +15 |
| ad_category 유사도 ≥ 0.35 | +10 |
| 객체 밀집도 ≥ 0.7 | −40 |

---

## 서버 배포

GitHub main 브랜치 push/merge 시 GitHub Actions로 자동 배포됩니다.
실제 서버 접속 정보 및 명령어는 `COMMANDS.md` (로컬 전용) 참조.
