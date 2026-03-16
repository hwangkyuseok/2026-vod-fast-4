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
  const rawW = (overlay.coordinates_w != null && overlay.coordinates_w > 0)
    ? overlay.coordinates_w * scaleX
    : videoDisplayWidth * 0.25;
  const rawH = (overlay.coordinates_h != null && overlay.coordinates_h > 0)
    ? overlay.coordinates_h * scaleY
    : videoDisplayHeight * 0.22;

  // ── Cap size: max 28 % of video display dimensions ────────────────────
  // Prevents very large "safe areas" from covering most of the screen.
  const MAX_W = videoDisplayWidth  * 0.28;
  const MAX_H = videoDisplayHeight * 0.28;
  const w = Math.min(rawW, MAX_W);
  const h = Math.min(rawH, MAX_H);

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
    // Softer appearance: rounded card with blur-backed border
    borderRadius:   "16px",
    overflow:       "hidden",
    boxShadow:      "0 4px 24px rgba(0,0,0,0.55)",
    border:         "1px solid rgba(255,255,255,0.18)",
    // Slight transparency so the underlying video is still visible
    opacity:        0.92,
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
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={overlay.ad_resource_url}
            alt={overlay.matched_ad_id}
            style={{
              width:      "100%",
              height:     "100%",
              objectFit:  "contain",
              background: "rgba(0,0,0,0.35)",
            }}
          />
        )}

        {/* Score badge (dev helper) */}
        <span
          style={{
            position:   "absolute",
            bottom:     4,
            right:      4,
            fontSize:   10,
            background: "rgba(0,0,0,0.55)",
            color:      "#fff",
            padding:    "1px 4px",
            borderRadius: 6,
          }}
        >
          score {overlay.score}
        </span>
      </div>

      {/* 피드백 버튼 — pointerEvents 별도 레이어 (재생 방해 없음) */}
      <div
        style={{
          position:      "absolute",
          left:          `${x}px`,
          top:           `${Math.max(0, y - 28)}px`,
          display:       "flex",
          gap:           4,
          zIndex:        11,
          pointerEvents: "auto",
        }}
      >
        {feedback === null ? (
          <>
            <button
              onClick={() => submitFeedback(1)}
              title="적합한 광고"
              style={feedbackBtnStyle("#16a34a")}
            >👍</button>
            <button
              onClick={() => submitFeedback(-1)}
              title="부적합한 광고"
              style={feedbackBtnStyle("#dc2626")}
            >👎</button>
          </>
        ) : (
          <span style={{
            fontSize: 11,
            background: "rgba(0,0,0,0.6)",
            color: feedback === 1 ? "#4ade80" : "#f87171",
            padding: "2px 6px",
            borderRadius: 6,
          }}>
            {feedback === 1 ? "✓ 적합" : "✗ 부적합"}
          </span>
        )}
      </div>
    </>
  );
}
