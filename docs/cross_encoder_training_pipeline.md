# Cross-Encoder 학습 파이프라인

> 최종 업데이트: 2026-03-31

---

## 개요

비디오 씬 분석 결과와 광고 소비욕구 간의 연결성을 평가하는
Cross-Encoder 모델(`cross-encoder/ms-marco-MiniLM-L-12-v2`)을
Fine-tuning하여 광고 매칭 품질을 향상시키는 파이프라인.

---

## 전체 흐름

```
step2 (영상 분석)
  └─ analysis_scene 테이블에 context_narrative + desire 저장
        ↓
  labeling_gemini.py
  └─ (씬, 광고) 쌍을 Gemini로 평가 → cross_encoder_labels 저장
        ↓
  train_cross_encoder.py
  └─ split 할당 → Fine-tuning → 모델 저장 → 이력 기록
        ↓
  step4_decision/decision.py
  └─ Fine-tuned 모델로 실시간 광고 매칭 점수 계산
```

---

## DB 테이블

### cross_encoder_labels

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | SERIAL PK | |
| scene_id | INTEGER | analysis_scene.id 참조 |
| ad_id | VARCHAR(200) | ad_inventory.ad_id 참조 |
| context_narrative | TEXT | 씬 분석 내용 (상황/감정/욕구) |
| target_narrative | TEXT | 광고 소비욕구 설명 |
| gemini_score | FLOAT | Gemini 평가 점수 (0.0~1.0) |
| label | VARCHAR(20) | positive(≥0.7) / negative(≤0.3) / ambiguous |
| split | VARCHAR(5) | train / test / NULL(미할당) |
| trained_at | TIMESTAMP | 학습에 사용된 시각 (NULL=미사용) |
| created_at | TIMESTAMPTZ | 라벨 생성 시각 |

**split 규칙**
- 최초 학습 실행 시 `split=NULL` 행을 scene_id 단위 80/20 자동 할당 후 DB 영구 저장
- 한 번 할당된 split은 변경되지 않음 (중복 방지)
- 신규 라벨이 쌓이면 다음 학습 실행 시 새 씬만 추가 할당

**trained_at 규칙**
- `split='train'` 행 전체에 학습 완료 시 `NOW()` 기록
- 향후 증분 학습 구현 시 `trained_at IS NULL` 조건으로 신규 데이터 식별

### ce_training_runs

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | SERIAL PK | run_id |
| run_at | TIMESTAMP | 학습 시작 시각 |
| train_count | INTEGER | 학습 사용 샘플 수 |
| test_count | INTEGER | 평가 사용 샘플 수 |
| epochs | INTEGER | 학습 epoch 수 |
| model_path | TEXT | 모델 저장 경로 |

---

## 라벨링 (labeling_gemini.py)

### 데이터 소스
- **씬**: `analysis_scene WHERE desire IS NOT NULL AND desire <> ''`
- **광고**: `ad_inventory` (target_narrative 기준)

### Gemini 평가 프롬프트 (개선됨)
씬의 소비욕구(desire 항목)와 광고가 자극하는 소비욕구 간의
**연결성** 기준으로 평가 (시각적 유사성 배제)

### 점수 → 라벨 변환
| 점수 | 라벨 |
|------|------|
| ≥ 0.7 | positive |
| ≤ 0.3 | negative |
| 0.3 ~ 0.7 | ambiguous (학습 제외) |

### 실행 명령
```bash
cd /app/Docker/pipeline
docker-compose -f docker-compose.training.yml run --rm labeling
```

---

## 학습 (train_cross_encoder.py)

### 핵심 함수

#### `_assign_split_if_needed()`
- `split=NULL AND label IN ('positive','negative')` 인 씬 조회
- scene_id 단위 셔플 후 80% train / 20% test 할당
- DB UPDATE로 영구 저장 (이미 split 있는 행은 건드리지 않음)

#### `_load_train_data(neg_ratio=3)`
1. `_assign_split_if_needed()` 호출
2. `split='train'` 행 로드 → negative 다운샘플링 (positive 1: negative neg_ratio)
3. `split='test'` 행 로드 (평가 전용, 다운샘플링 없음)
4. `(train_rows, test_rows)` 반환

#### `_create_training_run()` / `_finalize_training_run()`
- 학습 전: `ce_training_runs INSERT` (run_id 획득)
- 학습 후: train_count / test_count UPDATE

#### `_mark_trained_at()`
- `split='train'` 전체에 `trained_at = NOW()` 기록

### 실행 명령
```bash
cd /app/Docker/pipeline

# 기본 (epochs=3, neg_ratio=3)
docker-compose -f docker-compose.training.yml run --rm training

# 옵션 지정
docker-compose -f docker-compose.training.yml run --rm training \
  --epochs 5 --neg-ratio 2
```

### 모델 저장 경로
- 컨테이너 내부: `/app/storage/models/cross_encoder`
- 호스트 실제 경로: `/app/HelloVision/data/storage/models/cross_encoder`

---

## 라벨 현황 (2026-03-31 기준)

| 항목 | 수치 |
|------|------|
| 전체 라벨 수 | 15,029건 |
| positive | 797건 (7.1%) |
| negative | 10,388건 (92.9%) |
| ambiguous | 3,844건 (제외) |
| 학습 대상 씬 수 | 1,002개 |
| 학습 대상 광고 수 | 699개 |
| neg_ratio=3 적용 시 train 샘플 | ~2,548건 |

> ⚠️ positive 비율이 낮음 (7.1%). 모델 학습 후 test set 평가 지표 반드시 확인 필요.

---

## 광고 매칭 연동 (step4_decision/decision.py)

Fine-tuned 모델은 실시간 광고 매칭에서 다음과 같이 사용:

```
Pre-filter (MiniLM 코사인 유사도)
  + desire 임베딩 블렌딩 (0.5 × context_sim + 0.5 × desire_sim)
        ↓
Cross-Encoder 재랭킹
  + desire 블렌딩 (CE 점수 + desire_lookup 점수)
        ↓
동적 광고 간격 (MIN_AD_INTERVAL)
  min(300, max(60, 60 × (영상길이 // 1800 + 1))) 초
        ↓
최종 광고 선택 및 오버레이
```

---

## 학습 후 step4 재시작

새 모델 로드를 위해 학습 완료 후 반드시 재시작 필요:

```bash
cd /app/Docker/pipeline
docker-compose restart step4
```

---

## 향후 고도화 방향

| 단계 | 내용 | 우선순위 |
|------|------|---------|
| 1 | 증분 학습: `trained_at IS NULL` 신규 데이터만 Fine-tuning | ★★★ |
| 2 | 리플레이 버퍼: 신규 N건 + 기존 랜덤 M건 혼합 (망각 방지) | ★★★ |
| 3 | 학습 버전별 `training_run_id` 컬럼으로 추적 | ★★ |
| 4 | Hard Negative Mining: 점수 0.3~0.5 구간 negative 집중 학습 | ★★ |

---

## 관련 파일

| 파일 | 위치 | 역할 |
|------|------|------|
| `labeling_gemini.py` | `backend/step4_training/` | Gemini 라벨링 |
| `train_cross_encoder.py` | `backend/step4_training/` | CE 학습 |
| `decision.py` | `backend/step4_decision/` | 광고 매칭 (CE 모델 사용) |
| `docker-compose.training.yml` | 프로젝트 루트 | 학습 컨테이너 실행 |
| `Dockerfile.training` | `backend/` | 학습용 이미지 빌드 |
