"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { OverlayEntry, OverlayMetadata } from "@/types/overlay";
import AdOverlayTV from "./AdOverlayTV";

interface TVPlayerProps {
  metadata: OverlayMetadata;
  onExit: () => void;
}

/**
 * TV 시현용 풀스크린 플레이어
 *
 * - 영상: 100% 화면 채움
 * - 광고 오버레이: 깔끔한 이미지/비디오만 (score, 피드백 없음)
 * - 진행바: 마우스 움직임/일시정지 시 하단 오버레이로 나타남
 * - 광고 시점: 진행바 위에 노란색 세로선 마커
 * - 3초 미입력 시 컨트롤 자동 숨김
 */
export default function TVPlayer({ metadata, onExit }: TVPlayerProps) {
  const videoRef     = useRef<HTMLVideoElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration]       = useState(0);
  const [isPlaying, setIsPlaying]     = useState(false);
  const [isMuted, setIsMuted]         = useState(false);
  const [isEnded, setIsEnded]         = useState(false);
  const [showControls, setShowControls] = useState(true);
  const [dimensions, setDimensions]   = useState({
    naturalWidth: 1920, naturalHeight: 1080,
    displayWidth: 0, displayHeight: 0,
  });

  const totalDur = duration || metadata.total_duration_sec || 1;

  // ── 시청 진행률 저장/복원 ────────────────────────────────────────────────
  const progressKey = metadata.job_id;

  const saveProgress = useCallback(() => {
    const v = videoRef.current;
    if (!v || !v.duration || v.duration === Infinity) return;
    try {
      const all = JSON.parse(localStorage.getItem("vod_watch_progress") || "{}");
      all[progressKey] = {
        currentTime: v.currentTime,
        duration: v.duration,
        percent: (v.currentTime / v.duration) * 100,
        updatedAt: Date.now(),
      };
      localStorage.setItem("vod_watch_progress", JSON.stringify(all));
    } catch { /* ignore */ }
  }, [progressKey]);

  useEffect(() => {
    const interval = setInterval(() => {
      if (videoRef.current && !videoRef.current.paused) saveProgress();
    }, 2000);
    const handleUnload = () => saveProgress();
    window.addEventListener("beforeunload", handleUnload);
    return () => {
      clearInterval(interval);
      window.removeEventListener("beforeunload", handleUnload);
      saveProgress();
    };
  }, [saveProgress]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const handleCanPlay = () => {
      try {
        const all = JSON.parse(localStorage.getItem("vod_watch_progress") || "{}");
        const saved = all[progressKey];
        if (saved && saved.currentTime > 0 && saved.percent < 98) {
          v.currentTime = saved.currentTime;
        }
      } catch { /* ignore */ }
    };
    v.addEventListener("canplay", handleCanPlay, { once: true });
    return () => v.removeEventListener("canplay", handleCanPlay);
  }, [progressKey]);

  // ── 컨트롤 자동 숨김 (3초) ──────────────────────────────────────────────
  const resetHideTimer = useCallback(() => {
    setShowControls(true);
    if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
    hideTimerRef.current = setTimeout(() => {
      if (videoRef.current && !videoRef.current.paused) {
        setShowControls(false);
      }
    }, 3000);
  }, []);

  useEffect(() => {
    resetHideTimer();
    return () => { if (hideTimerRef.current) clearTimeout(hideTimerRef.current); };
  }, [resetHideTimer]);

  // ── seek 헬퍼 ───────────────────────────────────────────────────────────
  const seekTo = useCallback((t: number) => {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = Math.max(0, Math.min(t, v.duration || Infinity));
    setCurrentTime(v.currentTime);
    setIsEnded(false);
  }, []);

  // ── 키보드: ←/→ ±10s, Space 재생/정지 ──────────────────────────────────
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      resetHideTimer();

      if (e.key === "ArrowRight") {
        e.preventDefault();
        seekTo((videoRef.current?.currentTime ?? 0) + 10);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        seekTo((videoRef.current?.currentTime ?? 0) - 10);
      } else if (e.key === " ") {
        e.preventDefault();
        togglePlay();
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [seekTo, resetHideTimer]);

  // ── 마우스 움직임 → 컨트롤 표시 ─────────────────────────────────────────
  const handleMouseMove = useCallback(() => {
    resetHideTimer();
  }, [resetHideTimer]);

  // ── timeupdate ──────────────────────────────────────────────────────────
  const handleTimeUpdate = useCallback(() => {
    setCurrentTime(videoRef.current?.currentTime ?? 0);
  }, []);

  // ── 비디오 크기 감지 ────────────────────────────────────────────────────
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const update = () => {
      setDimensions({
        naturalWidth: video.videoWidth || 1920,
        naturalHeight: video.videoHeight || 1080,
        displayWidth: video.offsetWidth,
        displayHeight: video.offsetHeight,
      });
    };
    video.addEventListener("loadedmetadata", update);
    video.addEventListener("resize", update);
    const ro = new ResizeObserver(update);
    ro.observe(video);
    return () => {
      video.removeEventListener("loadedmetadata", update);
      video.removeEventListener("resize", update);
      ro.disconnect();
    };
  }, []);

  const handleLoadedMetadata = () => {
    setDuration(videoRef.current?.duration ?? metadata.total_duration_sec);
  };

  // ── 재생/정지 ───────────────────────────────────────────────────────────
  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) { v.play(); setIsPlaying(true); }
    else { v.pause(); setIsPlaying(false); }
  }, []);

  const toggleMute = () => {
    const v = videoRef.current;
    if (!v) return;
    v.muted = !v.muted;
    setIsMuted(v.muted);
  };

  // ── 진행바 클릭으로 seek ────────────────────────────────────────────────
  const handleProgressClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    seekTo(ratio * totalDur);
  };

  // ── 활성 광고 오버레이 ──────────────────────────────────────────────────
  const allActive: OverlayEntry[] = isEnded ? [] : (metadata.overlays ?? []).filter((o) => {
    const start = o.overlay_start_time_sec;
    const end = start + o.overlay_duration_sec;
    return currentTime >= start && currentTime < end;
  });
  const activeOverlays: OverlayEntry[] =
    allActive.length <= 1 ? allActive : [allActive.reduce((best, o) => (o.score >= best.score ? o : best))];

  // ── 시간 포맷 ──────────────────────────────────────────────────────────
  const fmt = (s: number) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = Math.floor(s % 60);
    if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${sec.toString().padStart(2, "0")}`;
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full bg-black"
      onMouseMove={handleMouseMove}
      onClick={(e) => {
        // 진행바 영역 클릭은 무시 (이벤트 버블링 방지)
        if ((e.target as HTMLElement).closest("[data-controls]")) return;
        togglePlay();
        resetHideTimer();
      }}
      style={{ cursor: showControls ? "default" : "none" }}
    >
      {/* ── 비디오 ──────────────────────────────────────────────────── */}
      <video
        ref={videoRef}
        src={metadata.original_video_url}
        className="w-full h-full object-contain"
        onTimeUpdate={handleTimeUpdate}
        onLoadedMetadata={handleLoadedMetadata}
        onPlay={() => { setIsPlaying(true); setIsEnded(false); resetHideTimer(); }}
        onPause={() => { setIsPlaying(false); saveProgress(); setShowControls(true); }}
        onEnded={() => { setIsPlaying(false); setIsEnded(true); setShowControls(true); }}
      />

      {/* ── 광고 오버레이 (깔끔, score/피드백 없음) ────────────────────── */}
      {activeOverlays.map((overlay) => (
        <AdOverlayTV
          key={`${overlay.matched_ad_id}-${overlay.overlay_start_time_sec}`}
          overlay={overlay}
          videoNaturalWidth={dimensions.naturalWidth}
          videoNaturalHeight={dimensions.naturalHeight}
          videoDisplayWidth={(dimensions.displayWidth || containerRef.current?.offsetWidth) ?? 1920}
          videoDisplayHeight={(dimensions.displayHeight || containerRef.current?.offsetHeight) ?? 1080}
          isPlaying={isPlaying}
        />
      ))}

      {/* ── 광고 재생 중 뱃지 ──────────────────────────────────────────── */}
      {activeOverlays.length > 0 && (
        <div
          className="absolute top-4 left-4 text-white text-xs px-3 py-1.5 rounded-md font-medium"
          style={{
            background: "rgba(230,0,18,0.85)",
            backdropFilter: "blur(8px)",
            transition: "opacity 0.3s",
            opacity: showControls ? 1 : 0.6,
          }}
        >
          광고 재생 중
        </div>
      )}

      {/* ── 뒤로가기 버튼 (좌상단) ─────────────────────────────────────── */}
      <button
        data-controls
        onClick={(e) => { e.stopPropagation(); onExit(); }}
        className="absolute top-4 right-4 text-white/70 hover:text-white transition-all"
        style={{
          opacity: showControls ? 1 : 0,
          pointerEvents: showControls ? "auto" : "none",
          transition: "opacity 0.3s ease",
        }}
      >
        <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>

      {/* ── 하단 컨트롤 오버레이 ───────────────────────────────────────── */}
      <div
        data-controls
        className="absolute bottom-0 left-0 right-0"
        style={{
          opacity: showControls ? 1 : 0,
          transform: showControls ? "translateY(0)" : "translateY(20px)",
          transition: "opacity 0.4s ease, transform 0.4s ease",
          pointerEvents: showControls ? "auto" : "none",
          background: "linear-gradient(to top, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0.4) 60%, transparent 100%)",
          padding: "60px 24px 20px 24px",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* ── 진행바 + 노란색 광고 마커 ────────────────────────────────── */}
        <div
          className="relative w-full h-[6px] rounded-full cursor-pointer group mb-4"
          style={{ background: "rgba(255,255,255,0.2)" }}
          onClick={handleProgressClick}
        >
          {/* 재생 진행 (보라 → 흰) */}
          <div
            className="absolute top-0 left-0 h-full rounded-full"
            style={{
              width: `${(currentTime / totalDur) * 100}%`,
              background: "linear-gradient(90deg, #7C3AED 0%, #FFFFFF 100%)",
              transition: "width 0.1s linear",
            }}
          />

          {/* ── 노란색 광고 시점 마커들 ─────────────────────────────────── */}
          {metadata.overlays.map((o) => {
            const pos = (o.overlay_start_time_sec / totalDur) * 100;
            const isActive =
              currentTime >= o.overlay_start_time_sec &&
              currentTime < o.overlay_start_time_sec + o.overlay_duration_sec;
            return (
              <div
                key={`marker-${o.matched_ad_id}-${o.overlay_start_time_sec}`}
                className="absolute top-[-3px] bottom-[-3px]"
                style={{
                  left: `${pos}%`,
                  width: isActive ? "4px" : "3px",
                  background: isActive ? "#FACC15" : "#EAB308",
                  borderRadius: "2px",
                  boxShadow: isActive ? "0 0 8px rgba(250,204,21,0.6)" : "none",
                  transition: "all 0.2s ease",
                }}
                title={`광고 ${fmt(o.overlay_start_time_sec)}`}
                onClick={(e) => {
                  e.stopPropagation();
                  seekTo(o.overlay_start_time_sec);
                }}
              />
            );
          })}

          {/* 재생 헤드 (원형) */}
          <div
            className="absolute top-1/2 -translate-y-1/2 w-[14px] h-[14px] rounded-full bg-white shadow-lg"
            style={{
              left: `${(currentTime / totalDur) * 100}%`,
              transform: "translate(-50%, -50%)",
              boxShadow: "0 0 10px rgba(255,255,255,0.5)",
            }}
          />
        </div>

        {/* ── 컨트롤 버튼들 ─────────────────────────────────────────────── */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            {/* 재생/정지 */}
            <button
              onClick={togglePlay}
              className="text-white hover:text-white/80 transition-colors"
            >
              {isPlaying ? (
                <svg className="w-8 h-8" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" />
                </svg>
              ) : (
                <svg className="w-8 h-8" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M8 5v14l11-7z" />
                </svg>
              )}
            </button>

            {/* -10s */}
            <button
              onClick={() => seekTo(currentTime - 10)}
              className="text-white/60 hover:text-white transition-colors text-xs font-mono
                         bg-white/10 hover:bg-white/20 px-2.5 py-1 rounded"
            >
              -10s
            </button>

            {/* +10s */}
            <button
              onClick={() => seekTo(currentTime + 10)}
              className="text-white/60 hover:text-white transition-colors text-xs font-mono
                         bg-white/10 hover:bg-white/20 px-2.5 py-1 rounded"
            >
              +10s
            </button>

            {/* 음소거 */}
            <button
              onClick={toggleMute}
              className="text-white/60 hover:text-white transition-colors"
            >
              {isMuted ? (
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M16.5 12A4.5 4.5 0 0 0 14 7.97v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51A8.796 8.796 0 0 0 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06A8.99 8.99 0 0 0 17.73 18l1.98 1.98L21 18.7 4.27 3zM12 4L9.91 6.09 12 8.18V4z" />
                </svg>
              ) : (
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 7.97v8.05c1.48-.73 2.5-2.25 2.5-4.02z" />
                </svg>
              )}
            </button>

            {/* 시간 */}
            <span className="text-sm text-white/80 font-mono">
              {fmt(currentTime)} / {fmt(totalDur)}
            </span>
          </div>

          <div className="flex items-center gap-3">
            {/* 광고 슬롯 수 */}
            <span className="text-xs text-white/40">
              광고 슬롯 {metadata.overlays.length}개
            </span>

            {/* ← → 힌트 */}
            <span className="text-[10px] text-white/30">
              ← → 10초 이동
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
