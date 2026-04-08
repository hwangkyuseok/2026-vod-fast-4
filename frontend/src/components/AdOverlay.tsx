"use client";

import { useEffect, useRef, useState } from "react";
import type { OverlayEntry } from "@/types/overlay";

interface AdOverlayProps {
  overlay: OverlayEntry;
  videoNaturalWidth: number;
  videoNaturalHeight: number;
  videoDisplayWidth: number;
  videoDisplayHeight: number;
  isPlaying: boolean;
}

export default function AdOverlay({
  overlay,
  videoNaturalWidth,
  videoNaturalHeight,
  videoDisplayWidth,
  videoDisplayHeight,
  isPlaying,
}: AdOverlayProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  // 피드백 상태: null=미제출, 1=적합, -1=부적합
  const [feedback, setFeedback] = useState<1 | -1 | null>(null);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (isPlaying) {
      v.play().catch(() => {});
    } else {
      v.pause();
    }
  }, [isPlaying]);

  const scaleX = videoNaturalWidth > 0 ? videoDisplayWidth / videoNaturalWidth : 1;
  const scaleY = videoNaturalHeight > 0 ? videoDisplayHeight / videoNaturalHeight : 1;

  const rawX = overlay.coordinates_x != null ? overlay.coordinates_x * scaleX : 0;
  const rawY = overlay.coordinates_y != null ? overlay.coordinates_y * scaleY : 0;
  // ── Size: 배너/비디오 동일한 DB 좌표 기반 + max 28% 제한 ──────────
  const rawW = (overlay.coordinates_w != null && overlay.coordinates_w > 0)
    ? overlay.coordinates_w * scaleX
    : videoDisplayWidth * 0.25;
  const rawH = (overlay.coordinates_h != null && overlay.coordinates_h > 0)
    ? overlay.coordinates_h * scaleY
    : videoDisplayHeight * 0.22;

  const MAX_W = videoDisplayWidth  * 0.28;
  const MAX_H = videoDisplayHeight * 0.28;

  // 이미지가 로드되기 전에는 safe area 좌표 기준으로 임시 판단
  const isPortrait = rawH > rawW;
  const isLeftSide = rawX < videoDisplayWidth / 2;
  const EDGE_MARGIN = 8;

  let w: number, h: number, x: number, y: number;

  if (isPortrait) {
    // 세로 광고: 가로 광고의 높이 제한치(MAX_H)만큼 너비 제한, 엣지 스냅
    w = Math.min(rawW, MAX_H);
    h = Math.min(rawH, videoDisplayHeight * 0.6);
    x = isLeftSide
      ? EDGE_MARGIN
      : videoDisplayWidth - w - EDGE_MARGIN;
    y = Math.min(rawY, videoDisplayHeight - h);
  } else {
    // 가로 광고: 기존 동작 유지
    w = Math.min(rawW, MAX_W);
    h = Math.min(rawH, MAX_H);
    x = Math.min(rawX, videoDisplayWidth  - w);
    y = Math.min(rawY, videoDisplayHeight - h);
  }

  const style: React.CSSProperties = {
    position:       "absolute",
    left:           `${x}px`,
    top:            `${y}px`,
    width:          `${w}px`,
    height:         `${h}px`,
    pointerEvents:  "none",
    zIndex:         10,
    borderRadius:   4,
    overflow:       "hidden",
    boxShadow:      "0 2px 8px rgba(0,0,0,0.4)",
    border:         "none",
    opacity:        1,
    // Smooth fade-in
    animation:      "adOverlayFadeIn 0.35s ease",
  };

  return (
    <>
      <style>{`
        @keyframes adOverlayFadeIn {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
      `}</style>
      <div style={style}>
        {overlay.ad_type === "video_clip" ? (
          <video
            ref={videoRef}
            src={overlay.ad_resource_url}
            muted
            playsInline
            style={{ width: "100%", height: "100%", objectFit: "contain", background: "transparent" }}
          />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={overlay.ad_resource_url}
            alt={overlay.matched_ad_id}
            style={{
              width:      "100%",
              height:     "100%",
              objectFit:  "cover",
              background: "transparent",
            }}
          />
        )}

      </div>

    </>
  );
}
