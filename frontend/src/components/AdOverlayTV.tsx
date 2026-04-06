"use client";

import { useEffect, useRef } from "react";
import type { OverlayEntry } from "@/types/overlay";

interface AdOverlayTVProps {
  overlay: OverlayEntry;
  videoNaturalWidth: number;
  videoNaturalHeight: number;
  videoDisplayWidth: number;
  videoDisplayHeight: number;
  isPlaying: boolean;
}

/**
 * TV 시현용 광고 오버레이 — 깔끔한 광고 이미지/비디오만 표시
 * score, thumbs up/down 등 분석용 UI 전부 제거
 */
export default function AdOverlayTV({
  overlay,
  videoNaturalWidth,
  videoNaturalHeight,
  videoDisplayWidth,
  videoDisplayHeight,
  isPlaying,
}: AdOverlayTVProps) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (isPlaying) v.play().catch(() => {});
    else v.pause();
  }, [isPlaying]);

  // 좌표 스케일링
  const scaleX = videoNaturalWidth > 0 ? videoDisplayWidth / videoNaturalWidth : 1;
  const scaleY = videoNaturalHeight > 0 ? videoDisplayHeight / videoNaturalHeight : 1;

  const rawX = overlay.coordinates_x != null ? overlay.coordinates_x * scaleX : 0;
  const rawY = overlay.coordinates_y != null ? overlay.coordinates_y * scaleY : 0;
  const rawW = (overlay.coordinates_w != null && overlay.coordinates_w > 0)
    ? overlay.coordinates_w * scaleX
    : videoDisplayWidth * 0.25;
  const rawH = (overlay.coordinates_h != null && overlay.coordinates_h > 0)
    ? overlay.coordinates_h * scaleY
    : videoDisplayHeight * 0.22;

  const MAX_W = videoDisplayWidth * 0.28;
  const MAX_H = videoDisplayHeight * 0.28;
  const w = Math.min(rawW, MAX_W);
  const h = Math.min(rawH, MAX_H);
  const x = Math.min(rawX, videoDisplayWidth - w);
  const y = Math.min(rawY, videoDisplayHeight - h);

  return (
    <>
      <style>{`
        @keyframes tvAdFadeIn {
          from { opacity: 0; transform: scale(0.95); }
          to   { opacity: 0.92; transform: scale(1); }
        }
      `}</style>

      <div
        style={{
          position: "absolute",
          left: `${x}px`,
          top: `${y}px`,
          width: `${w}px`,
          height: `${h}px`,
          pointerEvents: "none",
          zIndex: 10,
          borderRadius: "14px",
          overflow: "hidden",
          boxShadow: "0 4px 24px rgba(0,0,0,0.55)",
          border: "1px solid rgba(255,255,255,0.15)",
          opacity: 0.92,
          animation: "tvAdFadeIn 0.35s ease",
        }}
      >
        {overlay.ad_type === "video_clip" ? (
          <video
            ref={videoRef}
            src={overlay.ad_resource_url}
            muted
            playsInline
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={overlay.ad_resource_url}
            alt={overlay.matched_ad_id}
            style={{
              width: "100%",
              height: "100%",
              objectFit: "contain",
              background: "rgba(0,0,0,0.35)",
            }}
          />
        )}
      </div>
    </>
  );
}
