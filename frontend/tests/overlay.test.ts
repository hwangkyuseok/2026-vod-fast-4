/**
 * VOD 광고 오버레이 — 비즈니스 로직 단위 테스트
 *
 * TDD Red → Green → Refactor 사이클로 작성되었습니다.
 * 실행: npm test
 */

import { describe, test, expect } from "vitest";
import type { OverlayEntry } from "@/types/overlay";
import {
  formatTime,
  getActiveOverlays,
  deduplicateOverlays,
  scaleCoordinates,
  capOverlaySize,
  clampPosition,
  clampSeekTime,
  MAX_OVERLAY_RATIO,
} from "@/utils/overlay";

// ── 테스트용 오버레이 팩토리 ────────────────────────────────────────────────

function createOverlay(partial: Partial<OverlayEntry> = {}): OverlayEntry {
  return {
    decision_id: 1,
    matched_ad_id: "ad_001",
    ad_resource_url: "/ads/test.png",
    ad_type: "banner",
    overlay_start_time_sec: 10,
    overlay_duration_sec: 5,
    coordinates_x: 100,
    coordinates_y: 200,
    coordinates_w: 300,
    coordinates_h: 150,
    score: 80,
    ...partial,
  };
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. formatTime — 시간 포맷팅
// ═══════════════════════════════════════════════════════════════════════════

describe("formatTime", () => {
  test("0초는 0:00으로 표시된다", () => {
    expect(formatTime(0)).toBe("0:00");
  });

  test("65초는 1:05로 표시된다", () => {
    expect(formatTime(65)).toBe("1:05");
  });

  test("3661초는 1:01:01로 표시된다", () => {
    expect(formatTime(3661)).toBe("1:01:01");
  });

  test("59초는 0:59로 표시된다", () => {
    expect(formatTime(59)).toBe("0:59");
  });

  test("3600초는 1:00:00으로 표시된다", () => {
    expect(formatTime(3600)).toBe("1:00:00");
  });

  test("음수는 0:00으로 처리된다", () => {
    expect(formatTime(-10)).toBe("0:00");
  });

  test("소수점은 내림 처리된다", () => {
    expect(formatTime(65.9)).toBe("1:05");
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 2. getActiveOverlays — 활성 오버레이 필터링
// ═══════════════════════════════════════════════════════════════════════════

describe("getActiveOverlays", () => {
  const overlays: OverlayEntry[] = [
    createOverlay({ overlay_start_time_sec: 10, overlay_duration_sec: 5, score: 80 }),
    createOverlay({ overlay_start_time_sec: 30, overlay_duration_sec: 10, score: 60 }),
  ];

  test("현재 시간이 오버레이 범위 안이면 해당 오버레이를 반환한다", () => {
    const result = getActiveOverlays(overlays, 12, false);
    expect(result).toHaveLength(1);
    expect(result[0].overlay_start_time_sec).toBe(10);
  });

  test("현재 시간이 어떤 오버레이 범위에도 없으면 빈 배열을 반환한다", () => {
    const result = getActiveOverlays(overlays, 20, false);
    expect(result).toHaveLength(0);
  });

  test("영상이 끝났으면(isEnded=true) 빈 배열을 반환한다", () => {
    const result = getActiveOverlays(overlays, 12, true);
    expect(result).toHaveLength(0);
  });

  test("오버레이 시작 시점 정확히 일치하면 활성이다", () => {
    const result = getActiveOverlays(overlays, 10, false);
    expect(result).toHaveLength(1);
  });

  test("오버레이 종료 시점 정확히 일치하면 비활성이다 (start+duration)", () => {
    // 10초 시작 + 5초 길이 = 15초에 종료. 15초는 비활성
    const result = getActiveOverlays(overlays, 15, false);
    expect(result).toHaveLength(0);
  });

  test("빈 오버레이 배열이면 빈 배열을 반환한다", () => {
    const result = getActiveOverlays([], 12, false);
    expect(result).toHaveLength(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 3. deduplicateOverlays — 중복 오버레이 제거
// ═══════════════════════════════════════════════════════════════════════════

describe("deduplicateOverlays", () => {
  test("1개 이하면 그대로 반환한다", () => {
    const single = [createOverlay({ score: 50 })];
    expect(deduplicateOverlays(single)).toEqual(single);
    expect(deduplicateOverlays([])).toEqual([]);
  });

  test("여러 개면 최고 점수 1개만 반환한다", () => {
    const overlays = [
      createOverlay({ matched_ad_id: "ad_low", score: 30 }),
      createOverlay({ matched_ad_id: "ad_high", score: 90 }),
      createOverlay({ matched_ad_id: "ad_mid", score: 60 }),
    ];
    const result = deduplicateOverlays(overlays);
    expect(result).toHaveLength(1);
    expect(result[0].matched_ad_id).toBe("ad_high");
  });

  test("동점이면 마지막으로 비교된 것이 선택된다", () => {
    const overlays = [
      createOverlay({ matched_ad_id: "ad_first", score: 80 }),
      createOverlay({ matched_ad_id: "ad_second", score: 80 }),
    ];
    const result = deduplicateOverlays(overlays);
    expect(result).toHaveLength(1);
    // reduce에서 >= 이므로 뒤쪽이 선택됨
    expect(result[0].matched_ad_id).toBe("ad_second");
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 4. scaleCoordinates — 좌표 스케일링
// ═══════════════════════════════════════════════════════════════════════════

describe("scaleCoordinates", () => {
  const dimensions = {
    naturalWidth: 1920,
    naturalHeight: 1080,
    displayWidth: 960,
    displayHeight: 540,
  };

  test("50% 축소 시 좌표가 절반으로 줄어든다", () => {
    const overlay = createOverlay({
      coordinates_x: 100,
      coordinates_y: 200,
      coordinates_w: 300,
      coordinates_h: 150,
    });
    const result = scaleCoordinates(overlay, dimensions);
    expect(result.x).toBe(50);
    expect(result.y).toBe(100);
    expect(result.w).toBe(150);
    expect(result.h).toBe(75);
  });

  test("coordinates_x가 null이면 x=0으로 처리된다", () => {
    const overlay = createOverlay({ coordinates_x: null });
    const result = scaleCoordinates(overlay, dimensions);
    expect(result.x).toBe(0);
  });

  test("coordinates_w가 null이면 기본 비율(25%)이 적용된다", () => {
    const overlay = createOverlay({ coordinates_w: null });
    const result = scaleCoordinates(overlay, dimensions);
    expect(result.w).toBe(960 * 0.25);
  });

  test("coordinates_w가 0이면 기본 비율이 적용된다", () => {
    const overlay = createOverlay({ coordinates_w: 0 });
    const result = scaleCoordinates(overlay, dimensions);
    expect(result.w).toBe(960 * 0.25);
  });

  test("naturalWidth가 0이면 scale=1로 처리된다", () => {
    const zeroDim = { ...dimensions, naturalWidth: 0 };
    const overlay = createOverlay({ coordinates_x: 100 });
    const result = scaleCoordinates(overlay, zeroDim);
    expect(result.x).toBe(100);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 5. capOverlaySize — 최대 크기 제한 (28%)
// ═══════════════════════════════════════════════════════════════════════════

describe("capOverlaySize", () => {
  test("제한 미만이면 원래 크기를 유지한다", () => {
    const result = capOverlaySize(100, 80, 1000, 1000);
    expect(result.w).toBe(100);
    expect(result.h).toBe(80);
  });

  test("제한 초과 시 28%로 잘린다", () => {
    const result = capOverlaySize(500, 400, 1000, 1000);
    expect(result.w).toBe(1000 * MAX_OVERLAY_RATIO);
    expect(result.h).toBe(1000 * MAX_OVERLAY_RATIO);
  });

  test("너비만 초과해도 너비만 잘린다", () => {
    const result = capOverlaySize(500, 100, 1000, 1000);
    expect(result.w).toBe(280);
    expect(result.h).toBe(100);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 6. clampPosition — 화면 밖 방지
// ═══════════════════════════════════════════════════════════════════════════

describe("clampPosition", () => {
  test("화면 안에 있으면 원래 위치를 유지한다", () => {
    const result = clampPosition(100, 100, 200, 200, 1000, 1000);
    expect(result.x).toBe(100);
    expect(result.y).toBe(100);
  });

  test("오른쪽 밖으로 나가면 화면 끝으로 이동한다", () => {
    // x=900, w=200 → 900+200=1100 > 1000 → x를 800으로 클램핑
    const result = clampPosition(900, 100, 200, 200, 1000, 1000);
    expect(result.x).toBe(800);
  });

  test("아래쪽 밖으로 나가면 화면 끝으로 이동한다", () => {
    const result = clampPosition(100, 900, 200, 200, 1000, 1000);
    expect(result.y).toBe(800);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 7. clampSeekTime — Seek 시간 제한
// ═══════════════════════════════════════════════════════════════════════════

describe("clampSeekTime", () => {
  test("범위 안이면 그대로 반환한다", () => {
    expect(clampSeekTime(50, 100)).toBe(50);
  });

  test("음수면 0으로 제한된다", () => {
    expect(clampSeekTime(-10, 100)).toBe(0);
  });

  test("duration 초과면 duration으로 제한된다", () => {
    expect(clampSeekTime(150, 100)).toBe(100);
  });

  test("0은 그대로 0이다", () => {
    expect(clampSeekTime(0, 100)).toBe(0);
  });

  test("duration 정확히 일치하면 그대로 반환한다", () => {
    expect(clampSeekTime(100, 100)).toBe(100);
  });
});
