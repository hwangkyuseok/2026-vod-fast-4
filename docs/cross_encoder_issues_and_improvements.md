# Cross-Encoder 모델 이슈 및 개선 방안

> 작성일: 2026-03-31
> 학습 기준: run_id=2, epochs=3, train=2,580건, test=2,220건

---

## 현재 학습 결과

| Epoch | MAP | MRR@10 | NDCG@10 |
|-------|-----|--------|---------|
| 1 | 43.10 | 49.09 | 57.34 |
| 2 | 42.54 | 47.56 | 56.77 |
| 3 | 42.31 | 47.86 | 56.77 |

Epoch 1 이후 성능이 소폭 하락 → **과적합(Overfitting) 경향** 확인.

---

## 원인 분석

### 라벨 불균형

```
positive  :    797건  (7.1%)
negative  : 10,388건  (92.9%)
ambiguous :  3,844건  (학습 제외)

neg_ratio=3 적용 후 실제 학습:
  train — positive: 645건 / negative: 1,935건 (1:3)
  test  — positive: 152건 / negative: 2,068건
```

- positive 절대량이 너무 적어 모델이 "어울리는 쌍" 패턴 다양성을 충분히 학습하지 못함
- Epoch 1에서 이미 수렴, 이후 negative 편향으로 성능 소폭 하락

---

## 개선 방안

### 방안 1. neg_ratio 낮추기 ★ 즉시 가능

**의미**

| neg_ratio | train 구성 | 특징 |
|-----------|-----------|------|
| 3 (현재) | positive 645 + negative 1,935 | negative 과다 |
| 2 | positive 645 + negative 1,290 | 불균형 완화 |
| 1 | positive 645 + negative 645 | 1:1 균형 |

**방법** — 서버에서 직접 실행:
```bash
# 기존 모델 백업
cp -r /app/HelloVision/data/storage/models/cross_encoder \
      /app/HelloVision/data/storage/models/cross_encoder_bak_YYYYMMDD

# neg_ratio=1로 재학습
cd /app/Docker/pipeline
docker-compose -f docker-compose.training.yml run --rm training --neg-ratio 1
```

---

### 방안 2. Gemini 임계값 조정 (0.7 → 0.6) ★ 즉시 가능 (재라벨링 불필요)

**의미**
- 현재 gemini_score 0.6~0.7 구간은 ambiguous로 분류되어 학습 제외
- DB에 이미 저장된 score를 재활용하여 해당 구간을 positive로 편입

**방법** — DB UPDATE만으로 적용:
```sql
-- 1. 편입 예상 건수 확인
SELECT COUNT(*)
FROM cross_encoder_labels
WHERE gemini_score >= 0.6 AND gemini_score < 0.7 AND label = 'ambiguous';

-- 2. positive 편입 + split 초기화 (다음 학습 시 재할당)
UPDATE cross_encoder_labels
   SET label = 'positive',
       split = NULL,
       trained_at = NULL
 WHERE gemini_score >= 0.6 AND gemini_score < 0.7 AND label = 'ambiguous';
```
→ UPDATE 후 재학습 실행하면 자동 반영.

---

### 방안 3. ambiguous 점수 분포 확인 후 추가 편입 ★★ 보통

```sql
-- ambiguous 점수 분포 확인
SELECT
  ROUND(gemini_score::numeric, 1) AS score_bucket,
  COUNT(*) AS cnt
FROM cross_encoder_labels
WHERE label = 'ambiguous'
GROUP BY score_bucket
ORDER BY score_bucket DESC;
```
분포 확인 후 편입 임계값 결정.

---

### 방안 4. step2 추가 실행으로 씬 증가 ★ 중기

- 영상 추가 분석(step2) → `analysis_scene` 데이터 증가
- 라벨링 재실행 → 신규 씬에 대한 라벨 자동 생성
- 재학습 시 `split=NULL` 신규 행 자동 할당

```bash
# 라벨링 재실행 (기존 라벨 유지, 신규 씬만 처리)
docker-compose -f docker-compose.training.yml run --rm labeling
```

> 씬이 2배(2,000개)로 늘면 positive ~1,600건 예상 → 과적합 완화

---

## 권장 적용 순서

```
1단계 — 즉시 (데이터 변경 없이)
  └─ 방안 2: gemini_score 0.6~0.7 → positive 편입 (DB UPDATE)
  └─ 방안 1: neg_ratio=1 또는 2로 재학습

2단계 — 중기 (step2 영상 추가 후)
  └─ 방안 4: 라벨링 재실행 (신규 씬 자동 처리)
  └─ 방안 1+2 재적용 후 재학습

3단계 — 선택
  └─ 방안 3: ambiguous 추가 편입 검토
```

---

## 현재 상태 운영 가능 여부

**운영 가능합니다.**

- Fine-tuned 모델이 베이스 모델보다는 한국어 씬-광고 욕구 연결성을 더 잘 평가
- step4 컨테이너에 새 모델 로드 완료 (2026-03-31 21:10)
- 광고 매칭 파이프라인 정상 작동 중
- 개선은 점진적으로 적용 가능 (운영 중단 불필요)

---

## 관련 파일

| 파일 | 위치 |
|------|------|
| `labeling_gemini.py` | `backend/step4_training/` |
| `train_cross_encoder.py` | `backend/step4_training/` |
| `cross_encoder_training_pipeline.md` | `docs/` |
