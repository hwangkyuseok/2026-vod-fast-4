# 코딩 컨벤션 — VOD Ad Overlay Frontend

## 적용 범위

이 지침은 `src/**/*.ts`, `src/**/*.tsx` 파일에 적용됩니다.

## 네이밍 규칙

- **변수/함수**: camelCase (`formatTime`, `getActiveOverlays`)
- **컴포넌트**: PascalCase (`VideoPlayer`, `AdOverlay`)
- **타입/인터페이스**: PascalCase (`OverlayEntry`, `ScaledCoordinates`)
- **상수**: UPPER_SNAKE_CASE (`MAX_OVERLAY_RATIO`, `DEFAULT_DURATION`)

## TypeScript 규칙

- 모든 함수 파라미터와 반환값에 타입 명시
- `any` 대신 `unknown` 또는 구체적 타입 사용
- 인터페이스는 `src/types/`에 정의

## 함수 작성 규칙

- 한 함수는 한 가지 책임만 갖는다
- 순수 함수(side effect 없음)를 우선한다
- 매직 넘버 대신 이름 있는 상수를 사용한다

## 주석

- 함수 위에 JSDoc 형식으로 한국어 설명 작성
- 복잡한 계산식에는 왜 이렇게 계산하는지 이유를 주석으로 남긴다

## 예시

```typescript
/** 초(seconds)를 "h:mm:ss" 또는 "m:ss" 형식으로 변환한다 */
export function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}
```
