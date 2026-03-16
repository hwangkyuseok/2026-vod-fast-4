# VOD Dynamic Ad Overlay System

비디오 문맥 분석 기반 동적 광고 오버레이 시스템

---

## 아키텍처 개요

```
[FastAPI] ──POST /jobs──► [RabbitMQ: step1]
                                │
                    ┌───────────▼────────────┐
                    │  Step-1 Preprocessing  │  ffmpeg 프레임/오디오 추출
                    └───────────┬────────────┘
                                │ [step2]
                    ┌───────────▼────────────┐
                    │  Step-2 Analysis       │  Faster R-CNN + Qwen2-VL + librosa
                    └───────────┬────────────┘
                                │ [step3]
                    ┌───────────▼────────────┐
                    │  Step-3 Ad Matching    │  묵음 구간 × ad_inventory 매칭
                    └───────────┬────────────┘
                                │ [step4]
                    ┌───────────▼────────────┐
                    │  Step-4 Scoring        │  스코어링 → decision_result 저장
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
│   ├── common/             # 공통 유틸 (DB, RabbitMQ, config)
│   ├── step1_preprocessing/
│   ├── step2_analysis/     # Faster R-CNN, Qwen2-VL, 오디오
│   ├── step3_persistence/  # 광고 매칭
│   ├── step4_decision/     # 스코어링
│   ├── step5_api/          # FastAPI REST 서버
│   ├── init_schema.sql     # DB DDL
│   ├── init_db.py          # DB 초기화
│   └── populate_ad_inventory.py
└── frontend/               # Next.js 14 App
    └── src/
        ├── app/
        │   ├── page.tsx          # 홈 (작업 제출 / 플레이어 접근)
        │   └── player/[jobId]/   # VOD 플레이어 페이지
        ├── components/
        │   ├── VideoPlayer.tsx   # HTML5 비디오 + 오버레이 렌더링
        │   └── AdOverlay.tsx     # 단일 광고 오버레이 (영상/이미지)
        └── types/overlay.ts      # TypeScript 타입 정의
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
python populate_ad_inventory.py
```

### 4. 파이프라인 서비스 시작 (터미널 4개)

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

### 5. FastAPI 서버 시작

```bash
python -m step5_api.server
# → http://localhost:8000
```

### 6. Next.js 프론트엔드 시작

```bash
cd frontend
cp .env.local.example .env.local
npm install
npm run dev
# → http://localhost:3000
```

### 7. 작업 제출 (API)

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"video_path": "C:\\path\\to\\your\\video.mp4"}'
```

응답 예시:
```json
{"job_id": "550e8400-e29b-41d4-a716-446655440000", "status": "pending"}
```

### 8. 처리 상태 확인

```bash
curl http://localhost:8000/jobs/{job_id}
```

### 9. 오버레이 메타데이터 조회

```bash
curl http://localhost:8000/overlay/{job_id}
```

---

## DB 테이블 구조

| 테이블 | 설명 |
|--------|------|
| `job_history` | 작업 이력 및 상태 관리 |
| `video_preprocessing_info` | 전처리 메타데이터 (경로, FPS, 해상도 등) |
| `analysis_vision_context` | 프레임별 비전 분석 결과 (safe area, 밀집도, 장면 설명) |
| `analysis_audio` | 묵음 구간 타임스탬프 |
| `ad_inventory` | 광고 소재 목록 |
| `decision_result` | 최종 광고 삽입 결정 (좌표, 점수, 시간) |

---

## 스코어링 기준

| 조건 | 점수 |
|------|------|
| 묵음 구간 ≥ 광고 길이 | +30 |
| 장면 전환 후 1~2초 이내 | +20 |
| 객체 밀집도 ≤ 0.3 | +20 |
| 상황 텍스트 × target_mood 키워드 일치당 | +10 |
| 객체 밀집도 ≥ 0.7 | −40 |
