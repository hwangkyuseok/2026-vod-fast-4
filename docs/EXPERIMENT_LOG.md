# Step3 / Step4-A 실험 로그

> **담당 단계**: Step3 (씬×광고 cross-join candidate 빌드) + Step4-A (embedding scorer)
> **기준 모델**: `jhgan/ko-sroberta-multitask`
> **비교 대상**: 임베딩 모델 교체 / 유사도 임계값 튜닝

---

## 실험 요약 테이블

| EXP | 모델 | 임계값 | 후보 수 (avg) | Precision@5 | 정성 평가 | 비고 |
|-----|------|--------|--------------|-------------|-----------|------|
| EXP-001 | ko-sroberta-multitask | 0.30 | - | - | - | 기준선 |
| EXP-002 | MiniLM-L12-v2 | 0.30 | - | - | - | 모델 교체 |
| EXP-003 | ko-sroberta-multitask | 0.25 | - | - | - | 임계값 낮춤 |
| EXP-004 | ko-sroberta-multitask | 0.35 | - | - | - | 임계값 높임 |
| EXP-005 | ko-sroberta-multitask | 0.40 | - | - | - | 임계값 높임 |

> 정성 평가: ✅ 좋음 / △ 보통 / ❌ 나쁨

---

## EXP-001 — 기준선 (ko-sroberta, threshold=0.30)

**날짜**:
**실행 커맨드**:
```bash
python experiments/step3_4a/run_experiment.py --model ko-sroberta --threshold 0.30 --job_id <JOB_ID>
```

### 설정
| 항목 | 값 |
|------|----|
| 모델 | `jhgan/ko-sroberta-multitask` |
| 임계값 | 0.30 |
| 배치 크기 | 32 |

### Step3 결과 (candidate 빌드)
| 항목 | 값 |
|------|----|
| 총 씬 수 | |
| 총 광고 수 | |
| 총 candidate 수 | |
| 씬당 평균 candidate | |
| 빌드 소요 시간 | |

### Step4-A 결과 (embedding scorer)
| 항목 | 값 |
|------|----|
| 평균 유사도 score | |
| 상위 5개 평균 score | |
| score 분포 (min/max) | |
| 스코어링 소요 시간 | |

### 대표 결과 예시
```
씬:
광고:
유사도:
```

### 메모


---

## EXP-002 — 모델 교체 (MiniLM-L12-v2, threshold=0.30)

**날짜**:
**실행 커맨드**:
```bash
python experiments/step3_4a/run_experiment.py --model minilm --threshold 0.30 --job_id <JOB_ID>
```

### 설정
| 항목 | 값 |
|------|----|
| 모델 | `paraphrase-multilingual-MiniLM-L12-v2` |
| 임계값 | 0.30 |
| 배치 크기 | 32 |

### Step3 결과
| 항목 | 값 |
|------|----|
| 총 씬 수 | |
| 총 광고 수 | |
| 총 candidate 수 | |
| 씬당 평균 candidate | |
| 빌드 소요 시간 | |

### Step4-A 결과
| 항목 | 값 |
|------|----|
| 평균 유사도 score | |
| 상위 5개 평균 score | |
| score 분포 (min/max) | |
| 스코어링 소요 시간 | |

### EXP-001 대비 비교
| 항목 | EXP-001 (ko-sroberta) | EXP-002 (MiniLM) | 차이 |
|------|-----------------------|------------------|------|
| 평균 score | | | |
| 상위 5개 score | | | |
| 소요 시간 | | | |

### 메모


---

## EXP-003 — 임계값 낮춤 (ko-sroberta, threshold=0.25)

**날짜**:
**실행 커맨드**:
```bash
python experiments/step3_4a/run_experiment.py --model ko-sroberta --threshold 0.25 --job_id <JOB_ID>
```

### 설정
| 항목 | 값 |
|------|----|
| 모델 | `jhgan/ko-sroberta-multitask` |
| 임계값 | 0.25 |

### Step4-A 결과
| 항목 | 값 |
|------|----|
| 평균 유사도 score | |
| 상위 5개 평균 score | |
| score 분포 (min/max) | |

### EXP-001 대비 비교
| 항목 | EXP-001 (0.30) | EXP-003 (0.25) | 변화 |
|------|----------------|----------------|------|
| 후보 수 | | | |
| 평균 score | | | |
| 잘못 통과된 후보 | | | |

### 메모


---

## EXP-004 — 임계값 높임 (ko-sroberta, threshold=0.35)

**날짜**:
**실행 커맨드**:
```bash
python experiments/step3_4a/run_experiment.py --model ko-sroberta --threshold 0.35 --job_id <JOB_ID>
```

### Step4-A 결과
| 항목 | 값 |
|------|----|
| 평균 유사도 score | |
| 상위 5개 평균 score | |
| 후보 탈락률 (vs EXP-001) | |

### 메모


---

## EXP-005 — 임계값 더 높임 (ko-sroberta, threshold=0.40)

**날짜**:
**실행 커맨드**:
```bash
python experiments/step3_4a/run_experiment.py --model ko-sroberta --threshold 0.40 --job_id <JOB_ID>
```

### Step4-A 결과
| 항목 | 값 |
|------|----|
| 평균 유사도 score | |
| 상위 5개 평균 score | |
| 후보 탈락률 (vs EXP-001) | |

### 메모


---

## 임계값 튜닝 요약

| 임계값 | 평균 후보 수 | 평균 score | 탈락률 | 정성 평가 |
|--------|-------------|------------|--------|-----------|
| 0.25 | | | | |
| 0.30 | | | 기준 | |
| 0.35 | | | | |
| 0.40 | | | | |

---

## 최종 채택 설정 및 근거

**채택 모델**:
**채택 임계값**:

### 채택 근거
1.
2.
3.

### 기각된 설정
| 설정 | 기각 이유 |
|------|-----------|
| | |

---

## 실험 결과 파일

`results/` 폴더에 각 실험별 JSON 저장됨:
- `results/exp001_ko-sroberta_th0.30.json`
- `results/exp002_minilm_th0.30.json`
- `results/exp003_ko-sroberta_th0.25.json`
- `results/exp004_ko-sroberta_th0.35.json`
- `results/exp005_ko-sroberta_th0.40.json`
