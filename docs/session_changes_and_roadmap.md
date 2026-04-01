# Step4 Decision — 이슈사항 / 변경사항 / 향후 과제

> 작성일: 2026-04-01
> 브랜치: fix/gemini-model-default
> 대상 파일: `backend/step4_decision/decision.py`, `backend/step4_decision/pre_filter.py`

---

## 1. 이슈사항 (Root Cause Analysis)

### Issue 1: Desire Blending이 MiniLM pre-filter를 통과시키지 못함

**증상**: 특정 job에서 광고 매칭 결과 0건

**원인**:

개선4(Desire Blending)에서 `pre-filter` 단계의 유사도 계산에 `scene.desire` 임베딩을 혼합했는데, scene의 desire 표현(추상적 욕구)과 ad의 `target_narrative`(구체적 구매 행동) 간의 의미 거리가 커서 블렌딩 후 유사도가 임계값 이하로 떨어짐.

```
기존 공식: blended = 0.5 × ctx_sim + 0.5 × d_sim
예시:     ctx_sim ≈ 0.52,  d_sim ≈ 0.25
결과:     0.5 × 0.52 + 0.5 × 0.25 = 0.385  →  threshold(0.40) 미달 → SKIP
```

**근본 원인**: `target_narrative` 컬럼 값이 scene의 desire 포맷과 정렬되지 않음. 광고 라벨링 시 Gemini 프롬프트가 desire 형식을 고려하지 않고 일반 narrative로 생성함.

---

### Issue 2: CE(Cross-Encoder) 점수가 scoring 루프에서 이중 필터링 발생

**증상**: pre-filter 통과 후에도 대부분의 후보가 최종 스코어 0 → 삽입 0건

**원인**:

```python
# (기존) CE 점수 dict이 sim_lookup을 덮어씀
sim_lookup = dict(zip(unique_pairs, CE_scores))   # CE 점수 = 0.1 ~ 0.3

# scoring 루프에서 CE 점수를 precomputed_similarity로 사용
precomputed = sim_lookup.get((ctx, tgt))           # ≈ 0.22

# _score_candidate 내부 pre_filter.passes() 재실행
# → 0.22 < threshold(0.38) → SKIP  (이미 통과했는데 또 필터링)
```

MiniLM 단계에서 블렌딩 후 통과한 후보를 CE 점수(원본, 낮음)로 다시 평가하여 전부 탈락시킴.

---

### Issue 3: Docker 배포 경로 오류 (scp vs docker cp)

**증상**: decision.py 수정 후 배포했는데 서버에서 변경이 반영되지 않음

**원인**:
- `scp`로 호스트 경로(`/app/Docker/pipeline/backend/...`)에 복사
- Step4 컨테이너는 이미지 빌드 시 소스를 포함 (볼륨 마운트 없음)
- 컨테이너 내부 실제 경로: `/app/step4_decision/decision.py`

`scp`로 호스트에 복사해도 실행 중인 컨테이너에는 전혀 반영되지 않음.

---

## 2. 변경사항 (Changes Applied)

### 2-1. Pre-filter Desire Blending 비율 조정

**파일**: `backend/step4_decision/decision.py` (line ~609)

```python
# 변경 전
minilm_lookup[(ctx, tgt)] = 0.5 * ctx_sim + 0.5 * d_sim

# 변경 후
minilm_lookup[(ctx, tgt)] = 0.7 * ctx_sim + 0.3 * d_sim
```

**효과**:
```
변경 전: 0.5 × 0.52 + 0.5 × 0.25 = 0.385 → SKIP
변경 후: 0.7 × 0.52 + 0.3 × 0.25 = 0.439 → PASS (threshold 0.40)
```

---

### 2-2. CE 단계 Desire Blending 비활성화

**파일**: `backend/step4_decision/decision.py` (line ~662)

CE 재랭킹 단계에서 desire 블렌딩을 완전히 제거. CE 점수는 순수 semantic similarity로만 사용.

```python
# 변경 전 (제거됨)
# sim_lookup[key] = 0.5 * ce_score + 0.5 * d_sim

# 변경 후
scores = cross_encoder_scorer.batch_score(unique_pairs)
sim_lookup = dict(zip(unique_pairs, scores))
# CE 단계 desire 블렌딩 비활성화
# (pre-filter에서만 0.7/0.3 블렌딩 적용, CE 점수는 순수 사용)
```

---

### 2-3. Scoring 루프: precomputed_similarity 소스 우선순위 변경

**파일**: `backend/step4_decision/decision.py` (line ~736)

scoring 루프에서 CE 점수 대신 MiniLM(desire-blended) 점수를 우선 사용:

```python
# 변경 전
precomputed = sim_lookup.get((ctx, tgt))   # CE 점수(낮음) → 이중 필터링 발생

# 변경 후
ml_val = minilm_lookup.get((ctx, tgt))     # MiniLM 블렌딩 점수 (threshold 통과 보장)
sl_val = sim_lookup.get((ctx, tgt))         # CE 점수 (fallback)
precomputed = ml_val if ml_val is not None else sl_val
```

---

### 2-4. _score_candidate 내부 pre_filter 이중 평가 제거

**파일**: `backend/step4_decision/decision.py` (line ~271)

`precomputed_similarity`가 주어진 경우, 내부 `pre_filter.passes()` 임계값 재확인 건너뜀:

```python
# 변경 전: precomputed가 있어도 passes()를 다시 호출하여 CE 점수로 재필터링
passed, similarity = pre_filter.passes(candidate, precomputed_similarity)
if not passed:
    return 0, None, similarity

# 변경 후: precomputed가 있으면 threshold 재확인 생략
if precomputed_similarity is not None:
    similarity = precomputed_similarity
else:
    passed, similarity = pre_filter.passes(candidate, None)
    if not passed:
        return 0, None, similarity
```

---

### 2-5. Docker 배포 방법 수정

**문서**: `docs/decision_desire_blending_fix.md` 배포 절차 수정

`scp` → `docker cp` 방식으로 변경:

```bash
# 올바른 배포 방법 (볼륨 마운트 없음)
docker cp backend/step4_decision/decision.py \
    pipeline-step4-1:/app/step4_decision/decision.py
docker-compose restart step4
```

---

### 2-6. 최종 결과

위 변경 적용 후, 테스트 job에서 **7개 overlay 결과** 성공적으로 삽입 확인:
- `[v3.1]` 스코어 로그: 14~38점 범위
- 대표 광고: 미노티(36pts), 로체보보이스(38pts) 등

---

## 3. 향후 과제 (Roadmap)

### 단기 (즉시)

| 항목 | 내용 |
|------|------|
| 임계값 튜닝 검증 | 다양한 job ID로 desire blending 0.7/0.3 비율의 일반화 확인 |
| Docker 배포 자동화 | Makefile 또는 deploy.sh 스크립트에 `docker cp` 방식 명시 |
| pre_filter 로그 정리 | `[SIM]` 로그가 과다 출력됨 — INFO → DEBUG 레벨 조정 고려 |

---

### 중기 (1~2개월)

#### target_narrative 재생성

**문제**: 현재 ad의 `target_narrative`는 scene의 `desire` 포맷과 정렬되지 않음.

**목표**: Gemini 프롬프트 수정으로 광고 `target_narrative`를 scene desire 형식과 동일하게 재생성.

```
현재 scene.desire:        "패션 아이템을 검색하고 구매하고 싶어진다"  (추상적 욕구)
현재 ad.target_narrative: "뷰티 디바이스를 즉시 구매하고 싶어진다"  (구체적 행동)
목표 ad.target_narrative: "패션이나 뷰티에 관심이 생기고 구매를 고려하게 된다"  (desire 포맷 일치)
```

**작업 범위**:
- [ ] Gemini 광고 분석 프롬프트 수정
- [ ] `ad_inventory` 전체 재라벨링
- [ ] 재라벨링 후 desire_sim 분포 재측정

---

#### Cross-Encoder 재학습

**문제**: CE 모델이 현재 학습 데이터에 과적합 → 실제 추론 점수 낮음(0.1~0.3).

**목표**: 누적된 매칭 데이터 + neg_ratio 개선으로 정밀도 향상.

**참고 문서**: `docs/cross_encoder_issues_and_improvements.md`

**작업 범위**:
- [ ] 실제 overlay 결과(7건~)로 positive 학습 데이터 추가
- [ ] neg_ratio 실험 (현재 1:5 → 1:3 vs 1:7 비교)
- [ ] 재학습 후 CE 점수 분포 0.4 이상으로 개선 목표
- [ ] CE 점수만으로 threshold 통과 가능한 수준까지 향상 시 desire blending 제거 가능

---

### 장기 (3개월+)

#### Desire Blending 제거 및 CE 단독 파이프라인

현재 desire blending은 임시 보완책. 최종 목표:

```
현재: MiniLM(0.7×ctx + 0.3×desire) → CE(순수) → scoring
목표: MiniLM(ctx_sim) → CE(충분한 정밀도) → scoring  (desire blending 없음)
```

**선결 조건**:
1. CE 재학습으로 점수 분포 개선
2. target_narrative 재생성으로 semantic alignment 확보
3. EMBED_TOP_K_PER_SCENE, CE_TOP_K_PER_SCENE 파라미터 재조정

---

#### 실험 인프라 구축

- [ ] `docs/EXPERIMENT_LOG.md` 실험 결과 채우기 (EXP-001~005)
- [ ] A/B 비교를 위한 job별 매칭 결과 자동 집계 스크립트
- [ ] 씬 유형별(짧은/긴/객체 있음) 임계값 최적화 실험

---

## 관련 문서

| 파일 | 내용 |
|------|------|
| `docs/decision_desire_blending_fix.md` | Desire Blending 비율 조정 상세 |
| `docs/cross_encoder_issues_and_improvements.md` | CE 과적합 이슈 및 개선 방안 |
| `docs/cross_encoder_training_pipeline.md` | CE 학습 파이프라인 전체 |
| `docs/EXPERIMENT_LOG.md` | MiniLM 임계값 실험 로그 (작성 중) |
