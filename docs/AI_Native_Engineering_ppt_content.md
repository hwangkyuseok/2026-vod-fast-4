# AI Native Engineering + TDD 적용 — Frontend
> 이 문서를 기반으로 PPT **2장**을 제작해주세요.
> 디자인: 다크 네이비 배경(#161B2C), 흰색 텍스트, 포인트 색상 빨강(#E60012)
> 폰트: 제목 28~36pt Bold, 본문 14~16pt
> 레이아웃: 슬라이드 1은 좌→우 흐름, 슬라이드 2는 좌우 분할(Before/After)
> 아이콘/이모지 활용하여 시각적으로 보기 좋게 구성

---

## 슬라이드 1: 원래 코드 구조 → AI Native Engineering + TDD 적용

> **슬라이드 구성: 좌측(Before) → 중앙(적용 과정) → 우측은 "다음 슬라이드에서" 화살표**
> 좌측과 중앙 2칸 레이아웃, 큰 화살표(→)로 연결

---

### 좌측: 원래 코드 구조 (Before)

**제목: "적용 전 코드 구조"** (빨강 #E60012 포인트)

```
src/components/
├── VideoPlayer.tsx    ← 로직 + UI 혼재
├── TVPlayer.tsx       ← 동일 로직 복사-붙여넣기
├── AdOverlay.tsx      ← 계산 로직 내장
└── AdOverlayTV.tsx    ← 동일 계산 복사-붙여넣기
```

**문제점 3가지** (빨간 X 아이콘으로 표시)
- **중복 코드**: 동일 로직이 2~4곳에 복사-붙여넣기
- **매직 넘버**: `0.28`, `0.25`, `0.22` — 의미 불명
- **테스트 없음**: 브라우저 수동 확인만 가능, 경계값 검증 불가

---

### 우측: AI Native Engineering + TDD 적용 과정

**제목: "AI Native Engineering 적용"** (빨강 #E60012 포인트)

**핵심 4요소** (아이콘 카드 4개, 2x2 배치)

| 요소 | 설명 |
|------|------|
| **Custom Instructions** | `.github/copilot-instructions.md` — 프로젝트 규칙을 AI에게 전달 |
| **Custom Agents** | `.github/agents/TDD-*.agent.md` — AI에게 역할 분리 부여 |
| **Context Engineering** | 에이전트 내 역할/제약/완료조건 구조화 |
| **TDD** | Red(실패 테스트) → Green(최소 구현) → Refactor(개선) 사이클 |

**TDD 에이전트 체인** (화살표 플로우, 원형 순환)

```
🔴 TDD Red Agent        🟢 TDD Green Agent       🔵 TDD Refactor Agent
  실패 테스트 작성   →     최소 구현 작성     →      코드 개선
  (구현 수정 금지)        (전체 테스트 통과)         (테스트 유지)
        ↑                                              │
        └──────────────────────────────────────────────┘
```

**분리한 순수 함수 7개** (작은 태그/뱃지 형태로 나열)

`formatTime` · `getActiveOverlays` · `deduplicateOverlays` · `scaleCoordinates` · `capOverlaySize` · `clampPosition` · `clampSeekTime`

---

## 슬라이드 2: 적용 후 구조 및 개선 사항

> **슬라이드 구성: 좌측(After 구조) + 우측(개선 수치/효과)**
> 좌우 분할 레이아웃

---

### 좌측: 적용 후 코드 구조 (After)

**제목: "적용 후 코드 구조"** (빨강 #E60012 포인트)

```
src/
├── components/            ← UI 렌더링만 담당 (utils import 완료)
│   ├── VideoPlayer.tsx    ← formatTime, getActiveOverlays 등 사용
│   ├── TVPlayer.tsx       ← formatTime, getActiveOverlays 등 사용
│   ├── AdOverlay.tsx      ← scaleCoordinates, MAX_OVERLAY_RATIO 사용
│   └── AdOverlayTV.tsx    ← scaleCoordinates, capOverlaySize 등 사용
├── utils/overlay.ts       ← 비즈니스 로직 7개 함수, 1곳에서 관리
└── types/overlay.ts
tests/
├── overlay.test.ts        ← 순수 함수 테스트 32개
└── components/            ← 컴포넌트 테스트 24개
.github/
├── copilot-instructions.md
├── instructions/          ← 코딩/테스트 규칙 문서화
└── agents/                ← TDD 에이전트 3종 (Red/Green/Refactor)
```

**중복 코드 제거 결과** (화살표로 시각화)

| 로직 | Before | After |
|------|--------|-------|
| 시간 포맷 `fmt()` | **2곳** 복붙 | **1곳** |
| 오버레이 필터링 | **2곳** 복붙 | **1곳** |
| 좌표 스케일링 | **2곳** 복붙 | **1곳** |
| 크기 제한 / 경계 클램핑 | **2곳** 복붙 | **1곳** |

**매직 넘버 → 이름 있는 상수**

`0.28` → `MAX_OVERLAY_RATIO` · `0.25` → `DEFAULT_WIDTH_RATIO` · `0.22` → `DEFAULT_HEIGHT_RATIO`

---

### 우측: 개선 효과 종합

**제목: "개선 사항"** (빨강 #E60012 포인트)

**핵심 지표** (6칸 카드 형태)

| 항목 | Before | After |
|------|--------|-------|
| **코드 구조** | 로직+UI 혼재, 4곳 복붙 | 순수 함수 분리, 1곳 관리 |
| **매직 넘버** | `0.28`, `0.25` 의미 불명 | 이름 있는 상수 3개 |
| **품질 검증** | 브라우저 수동 확인 (수 분) | `npm test` 56개 자동 (2.29초) |
| **경계값 버그** | 시연 때 발견하면 대응 | 사전에 테스트로 고정 |
| **개발 규칙** | 암묵적 (머릿속) | `.github/` 파일로 문서화 |
| **AI 활용** | 비체계적 | 역할별 에이전트 사이클 |

**경계값 테스트 — 이전에는 검증 불가능했던 항목** (체크리스트)

| 케이스 | Before | After |
|--------|--------|-------|
| 광고 종료 15.000초에 정확히 꺼지는가 | ❓ 모름 | ✅ 통과 |
| 좌표 null 시 크래시 방지 | ❓ 모름 | ✅ 통과 |
| 광고 2개 겹칠 때 1개만 표시 | ❓ 모름 | ✅ 통과 |
| 음수 시간 입력 시 안전 처리 | ❓ 모름 | ✅ 통과 |
| 해상도 0 입력 시 폴백 처리 | ❓ 모름 | ✅ 통과 |

**테스트 결과 뱃지** (하단 강조 배너)

```
✅ 테스트 56개 전체 통과  |  4개 파일  |  2.29초
   Level 1: 순수 함수 32개 (함수 7개)
   Level 2: 컴포넌트 24개 (AdOverlayTV, Sidebar, PlayerPage)
```

**결론** (하단 빨간 강조 박스)

> **AI Native Engineering 적용으로 중복 코드 제거, 자동 테스트 56개, 팀 표준화를 달성하였다.**
