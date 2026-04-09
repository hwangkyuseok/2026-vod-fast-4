# 테스트 지침 — VOD Ad Overlay Frontend

## 적용 범위

이 지침은 `tests/**/*.test.ts`, `tests/**/*.test.tsx` 파일에 적용됩니다.

## 테스트 프레임워크

- **Vitest** 사용 (Jest 호환 API)
- 실행 명령: `npx vitest run`
- 감시 모드: `npx vitest`

## 테스트 작성 규칙

### 1. AAA 패턴 (Arrange-Act-Assert)

모든 테스트는 세 단계를 명확히 분리한다:

```typescript
test("formatTime: 65초는 1:05로 표시된다", () => {
  // Arrange
  const seconds = 65;

  // Act
  const result = formatTime(seconds);

  // Assert
  expect(result).toBe("1:05");
});
```

### 2. 한 테스트 = 한 동작

- 하나의 `test()` 함수는 하나의 동작만 검증한다
- 여러 동작을 한 테스트에 넣지 않는다

### 3. 테스트 함수명 규칙

`대상함수: 조건_결과` 형식으로 작성한다:

```typescript
test("getActiveOverlays: 현재 시간이 오버레이 범위 안이면 해당 오버레이를 반환한다", () => { ... });
test("getActiveOverlays: 영상이 끝났으면 빈 배열을 반환한다", () => { ... });
test("scaleCoordinates: 50% 축소 시 좌표가 절반으로 줄어든다", () => { ... });
```

### 4. 경계값 테스트 필수

비즈니스 로직에는 반드시 경계값(edge case) 테스트를 포함한다:

- 0초, 음수 시간
- null/undefined 좌표
- 빈 오버레이 배열
- 오버레이 시작 시점 정확히 일치
- 오버레이 종료 시점 정확히 일치

### 5. 테스트 그룹화

`describe`로 함수 단위 그룹을 만든다:

```typescript
describe("formatTime", () => {
  test("0초는 0:00으로 표시된다", () => { ... });
  test("65초는 1:05로 표시된다", () => { ... });
  test("3661초는 1:01:01로 표시된다", () => { ... });
});
```
