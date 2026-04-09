# VOD Ad Overlay System — Copilot 전역 지침

## 프로젝트 개요

VOD 영상 위에 AI가 분석한 최적의 광고를 오버레이로 삽입하는 서비스의 **Frontend (시현용 TV 플레이어)** 입니다.

## 기술 스택

- **Framework**: Next.js 14 (App Router)
- **Language**: TypeScript (strict)
- **UI**: React 18 + Tailwind CSS
- **Test**: Vitest + @testing-library/react
- **패키지 매니저**: npm

## 핵심 디렉터리 구조

```
src/
├── app/                 ← Next.js App Router 페이지
├── components/          ← React 컴포넌트 (VideoPlayer, TVPlayer, AdOverlay 등)
├── types/overlay.ts     ← OverlayEntry, OverlayMetadata 타입 정의
└── utils/overlay.ts     ← 순수 함수 (시간 포맷, 오버레이 필터링, 좌표 계산 등)
tests/
└── overlay.test.ts      ← 비즈니스 로직 단위 테스트
```

## 코딩 규칙

1. 변수명, 함수명은 영어 camelCase 사용
2. 코드 주석과 docstring은 한국어 작성
3. 모든 함수에 TypeScript 타입 명시
4. 비즈니스 로직은 `src/utils/`에 순수 함수로 분리하고, 컴포넌트에서 import하여 사용
5. 컴포넌트 내부에 복잡한 계산 로직을 직접 작성하지 않는다

## 금지 사항

- `any` 타입 사용 금지
- 테스트 없이 비즈니스 로직 추가 금지
- 기존 테스트를 깨뜨리는 변경 금지
