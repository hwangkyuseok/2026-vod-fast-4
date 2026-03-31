# Decision.py Desire Blending 비율 조정 계획

> 작성일: 2026-03-31
> 브랜치: fix/gemini-model-default
> 체크포인트 목적: 이 수정이 적절하지 않을 경우 git revert로 원상복구 가능

---

## 문제 요약

**증상**: 특정 job(`c1390050-359c-476b-8a59-362d6743df8e`)에서 광고 매칭 결과가 0건

**확인된 로그**:
```
pre_filter sim=0.19~0.29 → 전부 SKIP (threshold 0.40)
```

**실제 원인**: Desire Blending(개선4)이 pre-filter 유사도를 임계값 이하로 끌어내림

---

## 원인 분석

### Desire Blending 공식 (현재)

**pre-filter 단계** (`decision.py` ~604번 줄):
```python
minilm_lookup[(ctx, tgt)] = 0.5 * ctx_sim + 0.5 * d_sim
```

**CE 재랭킹 단계** (`decision.py` ~663번 줄):
```python
sim_lookup[key] = 0.5 * ce_score + 0.5 * d_sim
```

### 왜 실패하는가?

| 항목 | 예시 | 성격 |
|------|------|------|
| `scene.desire` | "패션 아이템을 검색하고 구매하고 싶어진다" | **추상적** — 씬에서 유발되는 소비욕구 |
| `ad.target_narrative` | "뷰티 디바이스를 즉시 구매하고 싶어진다" | **구체적** — 광고가 자극하는 구매행동 |

- 두 표현이 **다른 카테고리**의 욕구를 묘사 → 임베딩 유사도(`d_sim`)가 낮음 (~0.2~0.3)
- 기존 `ctx_sim`만 사용 시 ~0.52 → 임계값 통과
- Blending 후 `0.5 × 0.52 + 0.5 × 0.25 = 0.385` → **임계값(0.40) 미달**
- 결과: 모든 광고 후보 SKIP → 매칭 0건

### 근본 원인 (장기 해결 필요)

`target_narrative` 컬럼 값이 씬의 desire와 **같은 포맷으로 정렬되지 않음**.
→ Gemini 프롬프트를 수정하여 광고의 target_narrative를 씬 desire 형식과 동일하게 재생성해야 함.
→ 이는 전체 서비스 재구현이 필요하므로 **중장기 과제**로 분류.

---

## 단기 수정: Blending 비율 조정

Desire 신호를 완전히 제거하지 않고, 기존 유사도(ctx_sim, ce_score)에 가중치를 높임.

### 변경 내용

| 위치 | 기존 | 변경 후 |
|------|------|--------|
| pre-filter (minilm_lookup) | `0.5 × ctx_sim + 0.5 × d_sim` | `0.7 × ctx_sim + 0.3 × d_sim` |
| CE 재랭킹 (sim_lookup) | `0.5 × ce_score + 0.5 × d_sim` | `0.7 × ce_score + 0.3 × d_sim` |

### 기대 효과

```
기존: 0.5 × 0.52 + 0.5 × 0.25 = 0.385 → SKIP
변경: 0.7 × 0.52 + 0.3 × 0.25 = 0.439 → PASS (threshold 0.40)
```

---

## 롤백 방법

```bash
# 이 수정 전 체크포인트로 복귀
git revert HEAD
# 또는
git checkout checkpoint/before-desire-blending-fix -- backend/step4_decision/decision.py
```

---

## 배포 절차

```bash
# 1. 로컬 수정 완료 후 서버에 파일 전송
scp backend/step4_decision/decision.py \
    vhcalnplci@121.167.223.17:/app/HelloVision/backend/step4_decision/decision.py

# 2. step4 컨테이너 재시작 (새 코드 반영)
ssh vhcalnplci@121.167.223.17 \
    "cd /app/Docker/pipeline && docker-compose restart step4"

# 3. 테스트 — 동일 job 재실행
# http://121.167.223.17:3000/player/c1390050-359c-476b-8a59-362d6743df8e
```

---

## 장기 해결 로드맵

```
1단계 (현재) — 비율 조정 (0.5/0.5 → 0.7/0.3)
  └─ 즉시 적용 가능, 광고 매칭 복구 목적

2단계 (중기) — target_narrative 재생성
  └─ Gemini 프롬프트 수정: 광고 target_narrative를 씬 desire와 동일 포맷으로
  └─ ad_inventory 전체 재라벨링 → Cross-Encoder 재학습 필요
  └─ 전체 서비스 재구현 포함

3단계 (장기) — Cross-Encoder 재학습으로 대체
  └─ desire 블렌딩 없이 CE 점수만으로 충분한 품질 확보
  └─ 데이터 누적 후 positive 비율 개선 필요
```

---

## 관련 파일

| 파일 | 설명 |
|------|------|
| `backend/step4_decision/decision.py` | 수정 대상 |
| `docs/cross_encoder_issues_and_improvements.md` | CE 과적합 이슈 및 개선 방안 |
| `docs/cross_encoder_training_pipeline.md` | 학습 파이프라인 전체 문서 |
