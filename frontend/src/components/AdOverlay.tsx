"use client";

import { useEffect, useRef, useState } from "react";
import type { OverlayEntry } from "@/types/overlay";
import { scaleCoordinates, MAX_OVERLAY_RATIO } from "@/utils/overlay";

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
  const [adNaturalSize, setAdNaturalSize] = useState<{ w: number; h: number } | null>(null);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (isPlaying) {
      v.play().catch(() => { });
    } else {
      v.pause();
    }
  }, [isPlaying]);

  // ── 좌표 스케일링 (utils/overlay.ts 순수 함수 사용) ────────────────────
  const dims = { naturalWidth: videoNaturalWidth, naturalHeight: videoNaturalHeight, displayWidth: videoDisplayWidth, displayHeight: videoDisplayHeight };
  const raw = scaleCoordinates(overlay, dims);
  const scaleX = videoNaturalWidth > 0 ? videoDisplayWidth / videoNaturalWidth : 1;
  const scaleY = videoNaturalHeight > 0 ? videoDisplayHeight / videoNaturalHeight : 1;
  const rawX = raw.x;
  const rawW = raw.w;
  const rawH = raw.h;

  const MAX_W = videoDisplayWidth  * MAX_OVERLAY_RATIO;
  const MAX_H = videoDisplayHeight * MAX_OVERLAY_RATIO;

  // 광고 소재의 실제 비율 기준으로 크기 결정, 로드 전에는 safe area 좌표로 임시 사용
  const naturalW = adNaturalSize?.w ?? rawW;
  const naturalH = adNaturalSize?.h ?? rawH;
  const aspectRatio = naturalW > 0 && naturalH > 0 ? naturalW / naturalH : rawW / rawH;
  const isPortrait = naturalH > naturalW;
  const isLeftSide = rawX < videoDisplayWidth / 2;
  const EDGE_MARGIN = 30;
  const BOTTOM_MARGIN = 100;

  let w: number, h: number, x: number;

  if (isPortrait) {
    h = Math.min(naturalH * scaleY, MAX_H);
    w = h * aspectRatio;
    x = isLeftSide
      ? EDGE_MARGIN
      : videoDisplayWidth - w - EDGE_MARGIN;
  } else {
    w = Math.min(naturalW * scaleX, MAX_W);
    h = w / aspectRatio;
    if (h > MAX_H) { h = MAX_H; w = h * aspectRatio; }
    x = Math.min(rawX, videoDisplayWidth - w);
  }

  const style: React.CSSProperties = {
    position: "absolute",
    left: `${x}px`,
    bottom: `${BOTTOM_MARGIN}px`,
    width: `${w}px`,
    height: `${h}px`,
    pointerEvents: "none",
    zIndex: 10,
    borderRadius: 4,
    overflow: "hidden",
    background: "transparent",
    border: "none",
    opacity: 1,
    // Smooth fade-in
    animation: "adOverlayFadeIn 0.35s ease",
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
            onLoadedMetadata={(e) => {
              const v = e.currentTarget;
              if (v.videoWidth && v.videoHeight)
                setAdNaturalSize({ w: v.videoWidth, h: v.videoHeight });
            }}
            style={{ width: "100%", height: "100%", objectFit: "fill", background: "transparent" }}
          />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={overlay.ad_resource_url}
            alt={overlay.matched_ad_id}
            onLoad={(e) => {
              const img = e.currentTarget;
              if (img.naturalWidth && img.naturalHeight)
                setAdNaturalSize({ w: img.naturalWidth, h: img.naturalHeight });
            }}
            style={{ width: "100%", height: "100%", objectFit: "fill", background: "transparent" }}
          />
        )}

      </div>

    </>
  );
}
