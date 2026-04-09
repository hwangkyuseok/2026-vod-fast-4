"use client";

import { useEffect, useRef } from "react";
import type { OverlayEntry } from "@/types/overlay";
import { scaleCoordinates, capOverlaySize, clampPosition } from "@/utils/overlay";

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

  // 좌표 스케일링 (utils/overlay.ts 순수 함수 사용)
  const dims = { naturalWidth: videoNaturalWidth, naturalHeight: videoNaturalHeight, displayWidth: videoDisplayWidth, displayHeight: videoDisplayHeight };
  const raw = scaleCoordinates(overlay, dims);
  const capped = capOverlaySize(raw.w, raw.h, videoDisplayWidth, videoDisplayHeight);
  const pos = clampPosition(raw.x, raw.y, capped.w, capped.h, videoDisplayWidth, videoDisplayHeight);
  const { w, h } = capped;
  const { x, y } = pos;

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
