"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import type { OverlayEntry } from "@/types/overlay";

interface AdOverlayProps {
  overlay: OverlayEntry;
  /** Natural (unscaled) video width — used to compute display scale */
  videoNaturalWidth: number;
  videoNaturalHeight: number;
  /** Rendered video element dimensions (what the user sees) */
  videoDisplayWidth: number;
  videoDisplayHeight: number;
  /** 메인 영상 재생 상태 — 광고 비디오와 동기화 */
  isPlaying: boolean;
}

/**
 * Renders a single ad overlay (video or image) positioned absolutely
 * over the parent video container.
 *
 * Design principles:
 *  • Coordinates from the backend (safe_area_x/y/w/h) are in the natural
 *    video resolution → scaled to the displayed size.
 *  • Overlay is capped at 28 % of the display dimensions so it never
 *    dominates the screen even when the "safe area" is very large.
 *  • Large border-radius + drop-shadow gives a pill/card appearance that
 *    feels less intrusive than a sharp-edged rectangle.
 *  • Banner images use objectFit: contain (no crop / distortion).
 *  • Video clip ads play muted (sound-free), full clip duration.
 */
const feedbackBtnStyle = (hoverColor: string): React.CSSProperties => ({
  fontSize:     14,
  lineHeight:   1,
  padding:      "2px 5px",
  borderRadius: 6,
  border:       "1px solid rgba(255,255,255,0.3)",
  background:   "rgba(0,0,0,0.55)",
  color:        "#fff",
  cursor:       "pointer",
});

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

  // 메인 영상 재생/일시정지 상태와 광고 비디오 동기화
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (isPlaying) {
      v.play().catch(() => { /* autoplay blocked — ignore */ });
    } else {
      v.pause();
    }
  }, [isPlaying]);

  const submitFeedback = useCallback(async (label: 1 | -1) => {
    if (feedback !== null) return; // 중복 제출 방지
    setFeedback(label);
    try {
      await fetch(`/api/backend/feedback/${overlay.decision_id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label, source: "user" }),
      });
    } catch {
      // 피드백 실패는 UX에 영향 없이 무시
    }
  }, [feedback, overlay.decision_id]);

  // ── Scale coordinates from natural → display resolution ───────────────
  const scaleX =
    videoNaturalWidth > 0 ? videoDisplayWidth / videoNaturalWidth : 1;
  const scaleY =
    videoNaturalHeight > 0 ? videoDisplayHeight / videoNaturalHeight : 1;

  // Raw position from safe-area analysis
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
  const baseW = Math.min(rawW, MAX_W);
  const baseH = Math.min(rawH, MAX_H);

  // 이미지 비율에 맞게 컨테이너 축소 (fitSize가 있으면 적용)
  const w = fitSize ? fitSize.w : baseW;
  const h = fitSize ? fitSize.h : baseH;

  // Keep x/y inside the video boundaries after capping the size
  const x = Math.min(rawX, videoDisplayWidth  - w);
  const y = Math.min(rawY, videoDisplayHeight - h);

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
      {/* Keyframe animation injected once via a style tag */}
      <style>{`
        @keyframes adOverlayFadeIn {
          from { opacity: 0; transform: scale(0.95); }
          to   { opacity: 0.92; transform: scale(1); }
        }
      `}</style>

      {/* 광고 본체 (pointerEvents: none — 재생 방해 없음) */}
      <div style={style}>
        {overlay.ad_type === "video_clip" ? (
          <video
            ref={videoRef}
            src={overlay.ad_resource_url}
            muted          /* sound-free as requested */
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
