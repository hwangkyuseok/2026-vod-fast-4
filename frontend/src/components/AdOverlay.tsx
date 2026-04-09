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
  // 피드백 상태: null=미제출, 1=적합, -1=부적합
  const [feedback, setFeedback] = useState<1 | -1 | null>(null);
  // 이미지 실제 비율 기반 컨테이너 크기 조정
  const [fitSize, setFitSize] = useState<{ w: number; h: number } | null>(null);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (isPlaying) {
      v.play().catch(() => {});
    } else {
      v.pause();
    }
  }, [isPlaying]);

  // ── 좌표 스케일링 (utils/overlay.ts 순수 함수 사용) ────────────────────
  const dims = { naturalWidth: videoNaturalWidth, naturalHeight: videoNaturalHeight, displayWidth: videoDisplayWidth, displayHeight: videoDisplayHeight };
  const raw = scaleCoordinates(overlay, dims);
  const rawX = raw.x;
  const rawY = raw.y;
  const rawW = raw.w;
  const rawH = raw.h;

  const MAX_W = videoDisplayWidth  * MAX_OVERLAY_RATIO;
  const MAX_H = videoDisplayHeight * MAX_OVERLAY_RATIO;
  const baseW = Math.min(rawW, MAX_W);
  const baseH = Math.min(rawH, MAX_H);

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
            onLoad={(e) => {
              const img = e.currentTarget;
              const natW = img.naturalWidth;
              const natH = img.naturalHeight;
              if (natW > 0 && natH > 0) {
                const imgRatio = natW / natH;
                const boxRatio = baseW / baseH;
                let fitW: number, fitH: number;
                if (imgRatio > boxRatio) {
                  // 이미지가 더 넓음 → 폭 기준
                  fitW = baseW;
                  fitH = baseW / imgRatio;
                } else {
                  // 이미지가 더 높음 → 높이 기준
                  fitH = baseH;
                  fitW = baseH * imgRatio;
                }
                setFitSize({ w: fitW, h: fitH });
              }
            }}
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
