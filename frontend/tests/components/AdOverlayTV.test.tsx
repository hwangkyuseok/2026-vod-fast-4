/**
 * AdOverlayTV 컴포넌트 테스트
 *
 * TDD Red → Green → Refactor 사이클로 작성
 * 검증: 광고 타입별 렌더링, 좌표 스케일링, 크기 제한
 */

import { describe, test, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import AdOverlayTV from "@/components/AdOverlayTV";
import type { OverlayEntry } from "@/types/overlay";

// ── 테스트용 헬퍼 ──────────────────────────────────────────────────────────

function createOverlay(partial: Partial<OverlayEntry> = {}): OverlayEntry {
  return {
    decision_id: 1,
    matched_ad_id: "ad_test_001",
    ad_resource_url: "/ads/test-banner.png",
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

const DEFAULT_PROPS = {
  videoNaturalWidth: 1920,
  videoNaturalHeight: 1080,
  videoDisplayWidth: 960,
  videoDisplayHeight: 540,
  isPlaying: false,
};

// HTMLMediaElement.play/pause mock (jsdom에는 없음)
beforeAll(() => {
  HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
  HTMLMediaElement.prototype.pause = vi.fn();
});

// ═══════════════════════════════════════════════════════════════════════════
// 1. 광고 타입별 렌더링
// ═══════════════════════════════════════════════════════════════════════════

describe("AdOverlayTV: 광고 타입별 렌더링", () => {
  test("banner 타입이면 img 태그가 렌더링된다", () => {
    // Arrange
    const overlay = createOverlay({ ad_type: "banner", ad_resource_url: "/ads/banner.png" });

    // Act
    render(<AdOverlayTV overlay={overlay} {...DEFAULT_PROPS} />);

    // Assert
    const img = screen.getByAltText("ad_test_001");
    expect(img).toBeInTheDocument();
    expect(img.tagName).toBe("IMG");
    expect(img).toHaveAttribute("src", "/ads/banner.png");
  });

  test("video_clip 타입이면 video 태그가 렌더링된다", () => {
    // Arrange
    const overlay = createOverlay({ ad_type: "video_clip", ad_resource_url: "/ads/clip.mp4" });

    // Act
    const { container } = render(<AdOverlayTV overlay={overlay} {...DEFAULT_PROPS} />);

    // Assert
    const video = container.querySelector("video");
    expect(video).toBeInTheDocument();
    expect(video).toHaveAttribute("src", "/ads/clip.mp4");
  });

  test("video_clip은 muted 속성을 가진다 (소리 없음)", () => {
    // Arrange
    const overlay = createOverlay({ ad_type: "video_clip" });

    // Act
    const { container } = render(<AdOverlayTV overlay={overlay} {...DEFAULT_PROPS} />);

    // Assert: React는 muted를 DOM property로 설정하므로 .muted로 확인
    const video = container.querySelector("video") as HTMLVideoElement;
    expect(video).toBeTruthy();
    expect(video.muted).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 2. 좌표 스케일링 & 크기 제한
// ═══════════════════════════════════════════════════════════════════════════

describe("AdOverlayTV: 좌표 스케일링", () => {
  test("50% 축소 시 좌표가 절반으로 계산되어 스타일에 반영된다", () => {
    // Arrange: 1920→960 (50% 축소), 좌표 x=200
    const overlay = createOverlay({ coordinates_x: 200, coordinates_y: 100 });

    // Act
    const { container } = render(<AdOverlayTV overlay={overlay} {...DEFAULT_PROPS} />);

    // Assert: 오버레이 컨테이너의 left가 100px (200 * 0.5)
    const overlayDiv = container.querySelector("div > div") as HTMLElement;
    expect(overlayDiv.style.left).toBe("100px");
    expect(overlayDiv.style.top).toBe("50px");
  });

  test("오버레이 크기가 디스플레이의 28%를 초과하면 잘린다", () => {
    // Arrange: w=1000 → 스케일 후 500px, 28% 제한 = 960*0.28 = 268.8px
    const overlay = createOverlay({ coordinates_w: 1000, coordinates_h: 1000 });

    // Act
    const { container } = render(<AdOverlayTV overlay={overlay} {...DEFAULT_PROPS} />);

    // Assert
    const overlayDiv = container.querySelector("div > div") as HTMLElement;
    const width = parseFloat(overlayDiv.style.width);
    const height = parseFloat(overlayDiv.style.height);
    expect(width).toBeLessThanOrEqual(960 * 0.28 + 1);   // 268.8px 이내
    expect(height).toBeLessThanOrEqual(540 * 0.28 + 1);  // 151.2px 이내
  });

  test("coordinates가 null이면 기본 크기가 적용된다", () => {
    // Arrange
    const overlay = createOverlay({
      coordinates_x: null,
      coordinates_y: null,
      coordinates_w: null,
      coordinates_h: null,
    });

    // Act
    const { container } = render(<AdOverlayTV overlay={overlay} {...DEFAULT_PROPS} />);

    // Assert: 렌더링이 크래시 없이 완료됨
    const overlayDiv = container.querySelector("div > div") as HTMLElement;
    expect(overlayDiv).toBeInTheDocument();
    expect(overlayDiv.style.left).toBe("0px");
    expect(overlayDiv.style.top).toBe("0px");
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 3. 재생 동기화
// ═══════════════════════════════════════════════════════════════════════════

describe("AdOverlayTV: 재생 동기화", () => {
  test("isPlaying=true 이면 video.play()이 호출된다", () => {
    // Arrange
    const overlay = createOverlay({ ad_type: "video_clip" });

    // Act
    render(<AdOverlayTV overlay={overlay} {...DEFAULT_PROPS} isPlaying={true} />);

    // Assert
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalled();
  });

  test("isPlaying=false 이면 video.pause()가 호출된다", () => {
    // Arrange
    const overlay = createOverlay({ ad_type: "video_clip" });

    // Act
    render(<AdOverlayTV overlay={overlay} {...DEFAULT_PROPS} isPlaying={false} />);

    // Assert
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 4. 스타일 & 접근성
// ═══════════════════════════════════════════════════════════════════════════

describe("AdOverlayTV: 스타일", () => {
  test("오버레이는 pointerEvents: none으로 영상 재생을 방해하지 않는다", () => {
    // Arrange
    const overlay = createOverlay();

    // Act
    const { container } = render(<AdOverlayTV overlay={overlay} {...DEFAULT_PROPS} />);

    // Assert
    const overlayDiv = container.querySelector("div > div") as HTMLElement;
    expect(overlayDiv.style.pointerEvents).toBe("none");
  });

  test("오버레이는 zIndex: 10으로 영상 위에 표시된다", () => {
    // Arrange
    const overlay = createOverlay();

    // Act
    const { container } = render(<AdOverlayTV overlay={overlay} {...DEFAULT_PROPS} />);

    // Assert
    const overlayDiv = container.querySelector("div > div") as HTMLElement;
    expect(overlayDiv.style.zIndex).toBe("10");
  });
});
