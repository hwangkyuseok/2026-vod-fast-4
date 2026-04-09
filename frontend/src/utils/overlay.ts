/**
 * VOD 광고 오버레이 — 순수 함수 모음
 *
 * VideoPlayer, TVPlayer, AdOverlay, AdOverlayTV 컴포넌트에서
 * 공통으로 사용하는 비즈니스 로직을 순수 함수로 분리하였습니다.
 * TDD(Red → Green → Refactor) 사이클로 개발/검증됩니다.
 */

import type { OverlayEntry } from "@/types/overlay";

// ── 상수 ────────────────────────────────────────────────────────────────────

/** 오버레이 최대 크기 비율 (디스플레이 대비) */
export const MAX_OVERLAY_RATIO = 0.28;

/** 좌표가 null일 때 사용하는 기본 크기 비율 */
export const DEFAULT_WIDTH_RATIO = 0.25;
export const DEFAULT_HEIGHT_RATIO = 0.22;

// ── 시간 포맷 ───────────────────────────────────────────────────────────────

/** 초(seconds)를 "h:mm:ss" 또는 "m:ss" 형식으로 변환한다 */
export function formatTime(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const h = Math.floor(safe / 3600);
  const m = Math.floor((safe % 3600) / 60);
  const s = safe % 60;
  if (h > 0)
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

// ── 오버레이 필터링 ─────────────────────────────────────────────────────────

/** 현재 재생 시간에 활성 상태인 오버레이만 반환한다 */
export function getActiveOverlays(
  overlays: OverlayEntry[],
  currentTime: number,
  isEnded: boolean
): OverlayEntry[] {
  if (isEnded) return [];
  return overlays.filter((o) => {
    const start = o.overlay_start_time_sec;
    const end = start + o.overlay_duration_sec;
    return currentTime >= start && currentTime < end;
  });
}

/** 활성 오버레이가 여러 개일 때 최고 점수 1개만 선택한다 */
export function deduplicateOverlays(
  overlays: OverlayEntry[]
): OverlayEntry[] {
  if (overlays.length <= 1) return overlays;
  return [overlays.reduce((best, o) => (o.score >= best.score ? o : best))];
}

// ── 좌표 스케일링 ───────────────────────────────────────────────────────────

export interface ScaledCoordinates {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface VideoDimensions {
  naturalWidth: number;
  naturalHeight: number;
  displayWidth: number;
  displayHeight: number;
}

/** natural 해상도 기준 좌표를 display 크기로 스케일링한다 */
export function scaleCoordinates(
  overlay: Pick<
    OverlayEntry,
    "coordinates_x" | "coordinates_y" | "coordinates_w" | "coordinates_h"
  >,
  dimensions: VideoDimensions
): ScaledCoordinates {
  const scaleX =
    dimensions.naturalWidth > 0
      ? dimensions.displayWidth / dimensions.naturalWidth
      : 1;
  const scaleY =
    dimensions.naturalHeight > 0
      ? dimensions.displayHeight / dimensions.naturalHeight
      : 1;

  const x =
    overlay.coordinates_x != null ? overlay.coordinates_x * scaleX : 0;
  const y =
    overlay.coordinates_y != null ? overlay.coordinates_y * scaleY : 0;
  const w =
    overlay.coordinates_w != null && overlay.coordinates_w > 0
      ? overlay.coordinates_w * scaleX
      : dimensions.displayWidth * DEFAULT_WIDTH_RATIO;
  const h =
    overlay.coordinates_h != null && overlay.coordinates_h > 0
      ? overlay.coordinates_h * scaleY
      : dimensions.displayHeight * DEFAULT_HEIGHT_RATIO;

  return { x, y, w, h };
}

/** 오버레이 크기를 디스플레이의 28% 이내로 제한한다 */
export function capOverlaySize(
  w: number,
  h: number,
  displayWidth: number,
  displayHeight: number
): { w: number; h: number } {
  const maxW = displayWidth * MAX_OVERLAY_RATIO;
  const maxH = displayHeight * MAX_OVERLAY_RATIO;
  return {
    w: Math.min(w, maxW),
    h: Math.min(h, maxH),
  };
}

/** 오버레이가 화면 밖으로 나가지 않도록 위치를 클램핑한다 */
export function clampPosition(
  x: number,
  y: number,
  w: number,
  h: number,
  displayWidth: number,
  displayHeight: number
): { x: number; y: number } {
  return {
    x: Math.min(x, displayWidth - w),
    y: Math.min(y, displayHeight - h),
  };
}

// ── Seek 제한 ───────────────────────────────────────────────────────────────

/** 탐색 시간을 0 이상 duration 이하로 제한한다 */
export function clampSeekTime(time: number, duration: number): number {
  return Math.max(0, Math.min(time, duration));
}
