"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import type { OverlayEntry, OverlayMetadata } from "@/types/overlay";
import AdOverlay from "./AdOverlay";

interface VideoPlayerProps {
  metadata: OverlayMetadata;
}

interface DimensionState {
  naturalWidth: number;
  naturalHeight: number;
  displayWidth: number;
  displayHeight: number;
}

/**
 * VOD player with dynamic ad overlay rendering.
 *
 * Features:
 *  • ←/→ arrow keys → seek ±10 s
 *  • −10s / +10s buttons in controls bar
 *  • Ad list rows → click to jump to overlay start time
 *  • Timeline segments → click to jump
 *  • Active overlay detection via timeupdate (~4×/s)
 *  • Coordinate scaling: natural video resolution → displayed size
 */
export default function VideoPlayer({ metadata }: VideoPlayerProps) {
  const videoRef     = useRef<HTMLVideoElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration]       = useState(0);
  const [isPlaying, setIsPlaying]     = useState(false);
  const [isMuted, setIsMuted]         = useState(false);
  const [isEnded, setIsEnded]         = useState(false);
  const [dimensions, setDimensions]   = useState<DimensionState>({
    naturalWidth: 1920, naturalHeight: 1080,
    displayWidth: 0, displayHeight: 0,
  });

  // ── localStorage 시청 진행률 저장/복원 ───────────────────────────────────
  const progressKey = metadata.job_id; // job_id를 키로 사용

  // 진행률 저장 함수
  const saveProgress = useCallback(() => {
    const v = videoRef.current;
    if (!v || !v.duration || v.duration === Infinity) return;
    const progress = {
      currentTime: v.currentTime,
      duration: v.duration,
      percent: (v.currentTime / v.duration) * 100,
      updatedAt: Date.now(),
    };
    try {
      const all = JSON.parse(localStorage.getItem("vod_watch_progress") || "{}");
      all[progressKey] = progress;
      localStorage.setItem("vod_watch_progress", JSON.stringify(all));
    } catch { /* ignore */ }
  }, [progressKey]);

  // 주기적 저장 (2초마다) + pause/beforeunload 시 저장
  useEffect(() => {
    const interval = setInterval(() => {
      if (videoRef.current && !videoRef.current.paused) saveProgress();
    }, 2000);

    const handleUnload = () => saveProgress();
    window.addEventListener("beforeunload", handleUnload);

    return () => {
      clearInterval(interval);
      window.removeEventListener("beforeunload", handleUnload);
      saveProgress(); // 컴포넌트 언마운트 시에도 저장
    };
  }, [saveProgress]);

  // 마지막 시청 위치 복원
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

  // ── seek helper (used by arrow keys, buttons, ad list click) ────────────
  const seekTo = useCallback((t: number) => {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = Math.max(0, Math.min(t, v.duration || Infinity));
    setCurrentTime(v.currentTime);
    setIsEnded(false);  // resume overlay detection after manual seek
  }, []);

  // ── ←/→ arrow key: ±10 s ─────────────────────────────────────────────
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't interfere when the user is typing in an input
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "ArrowRight") {
        e.preventDefault();
        seekTo((videoRef.current?.currentTime ?? 0) + 10);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        seekTo((videoRef.current?.currentTime ?? 0) - 10);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [seekTo]);

  // ── timeupdate → track current time ──────────────────────────────────────
  const handleTimeUpdate = useCallback(() => {
    setCurrentTime(videoRef.current?.currentTime ?? 0);
  }, []);

  // ── resize observer → track rendered video size ───────────────────────────
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const updateDimensions = () => {
      setDimensions({
        naturalWidth:  video.videoWidth  || 1920,
        naturalHeight: video.videoHeight || 1080,
        displayWidth:  video.offsetWidth,
        displayHeight: video.offsetHeight,
      });
    };

    video.addEventListener("loadedmetadata", updateDimensions);
    video.addEventListener("resize", updateDimensions);

    const ro = new ResizeObserver(updateDimensions);
    ro.observe(video);

    return () => {
      video.removeEventListener("loadedmetadata", updateDimensions);
      video.removeEventListener("resize", updateDimensions);
      ro.disconnect();
    };
  }, []);

  // ── duration ──────────────────────────────────────────────────────────────
  const handleLoadedMetadata = () => {
    setDuration(videoRef.current?.duration ?? metadata.total_duration_sec);
  };

  // ── play / pause ──────────────────────────────────────────────────────────
  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) { v.play(); setIsPlaying(true); }
    else          { v.pause(); setIsPlaying(false); }
  };

  const toggleMute = () => {
    const v = videoRef.current;
    if (!v) return;
    v.muted = !v.muted;
    setIsMuted(v.muted);
  };

  // ── seek bar ──────────────────────────────────────────────────────────────
  const handleSeek = (e: React.ChangeEvent<HTMLInputElement>) => {
    seekTo(parseFloat(e.target.value));
  };

  // ── active overlays ───────────────────────────────────────────────────────
  // Collect all overlays whose window covers the current playback position.
  // When the video has ended, suppress all overlays immediately.
  const allActive: OverlayEntry[] = isEnded ? [] : (metadata.overlays ?? []).filter((o) => {
    const start = o.overlay_start_time_sec;
    const end   = start + o.overlay_duration_sec;
    return currentTime >= start && currentTime < end;
  });

  // Safety: never show more than one ad at a time.
  // The backend guarantees non-overlapping windows, but as a defensive layer
  // we pick the single highest-scoring overlay if duplicates slip through.
  const activeOverlays: OverlayEntry[] =
    allActive.length <= 1
      ? allActive
      : [allActive.reduce((best, o) => (o.score >= best.score ? o : best))];

  // ── time formatting (h:mm:ss or m:ss) ────────────────────────────────────
  const fmt = (s: number) => {
    const h   = Math.floor(s / 3600);
    const m   = Math.floor((s % 3600) / 60);
    const sec = Math.floor(s % 60);
    if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${sec.toString().padStart(2, "0")}`;
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  const totalDur = duration || metadata.total_duration_sec || 1;

  return (
    <div className="w-full flex flex-col items-center gap-4">
      {/* ── Video container ─────────────────────────────────────────────── */}
      <div
        ref={containerRef}
        className="relative w-full max-w-5xl bg-black rounded-xl overflow-hidden
                   shadow-2xl border border-gray-800"
        style={{ aspectRatio: "16/9" }}
      >
        <video
          ref={videoRef}
          src={metadata.original_video_url}
          className="w-full h-full object-contain"
          onTimeUpdate={handleTimeUpdate}
          onLoadedMetadata={handleLoadedMetadata}
          onPlay={() => { setIsPlaying(true); setIsEnded(false); }}
          onPause={() => { setIsPlaying(false); saveProgress(); }}
          onEnded={() => { setIsPlaying(false); setIsEnded(true); }}
        />

        {/* ── Ad overlays ──────────────────────────────────────────────── */}
        {activeOverlays.map((overlay) => (
          <AdOverlay
            key={`${overlay.matched_ad_id}-${overlay.overlay_start_time_sec}`}
            overlay={overlay}
            videoNaturalWidth={dimensions.naturalWidth}
            videoNaturalHeight={dimensions.naturalHeight}
            videoDisplayWidth={(dimensions.displayWidth || containerRef.current?.offsetWidth) ?? 1280}
            videoDisplayHeight={(dimensions.displayHeight || containerRef.current?.offsetHeight) ?? 720}
            isPlaying={isPlaying}
          />
        ))}

        {/* ── Overlay count badge ────────────────────────────────────────── */}
        {activeOverlays.length > 0 && (
          <div
            className="absolute top-3 left-3 bg-indigo-600/90 text-white text-xs
                        px-2 py-1 rounded-md font-medium backdrop-blur-sm"
          >
            광고 오버레이 재생 중
          </div>
        )}

        {/* ── Arrow key hint ─────────────────────────────────────────────── */}
        <div className="absolute bottom-3 right-3 text-[10px] text-gray-500/60 select-none pointer-events-none">
          ← → 10초 이동
        </div>
      </div>

      {/* ── Controls ────────────────────────────────────────────────────── */}
      <div className="w-full max-w-5xl bg-gray-900 rounded-xl px-4 py-3 border border-gray-800">
        {/* Seek bar */}
        <input
          type="range"
          min={0}
          max={totalDur}
          step={0.1}
          value={currentTime}
          onChange={handleSeek}
          className="w-full h-1.5 accent-indigo-500 cursor-pointer mb-3"
        />

        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            {/* Play / Pause */}
            <button
              onClick={togglePlay}
              className="text-white hover:text-indigo-400 transition-colors"
              aria-label={isPlaying ? "Pause" : "Play"}
            >
              {isPlaying ? (
                <svg className="w-7 h-7" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" />
                </svg>
              ) : (
                <svg className="w-7 h-7" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M8 5v14l11-7z" />
                </svg>
              )}
            </button>

            {/* −10 s button */}
            <button
              onClick={() => seekTo(currentTime - 10)}
              className="text-gray-400 hover:text-white transition-colors text-xs
                         font-mono bg-gray-800 hover:bg-gray-700 px-2 py-0.5 rounded"
              aria-label="10초 뒤로"
            >
              −10s
            </button>

            {/* +10 s button */}
            <button
              onClick={() => seekTo(currentTime + 10)}
              className="text-gray-400 hover:text-white transition-colors text-xs
                         font-mono bg-gray-800 hover:bg-gray-700 px-2 py-0.5 rounded"
              aria-label="10초 앞으로"
            >
              +10s
            </button>

            {/* Mute */}
            <button
              onClick={toggleMute}
              className="text-gray-400 hover:text-white transition-colors"
              aria-label={isMuted ? "Unmute" : "Mute"}
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

            {/* Time */}
            <span className="text-sm text-gray-400 font-mono">
              {fmt(currentTime)} / {fmt(totalDur)}
            </span>
          </div>

          {/* Overlay schedule info */}
          <span className="text-xs text-gray-500">
            광고 슬롯 {metadata.overlays.length}개
          </span>
        </div>
      </div>

      {/* ── Ad overlay timeline ──────────────────────────────────────────── */}
      {metadata.overlays.length > 0 && (
        <div className="w-full max-w-5xl">
          <h3 className="text-sm font-medium text-gray-400 mb-2">광고 타임라인</h3>
          <div className="relative h-6 bg-gray-800 rounded-full overflow-hidden">
            {metadata.overlays.map((o) => {
              const left  = (o.overlay_start_time_sec / totalDur) * 100;
              const width = Math.max(0.5, (o.overlay_duration_sec / totalDur) * 100);
              const isActive =
                currentTime >= o.overlay_start_time_sec &&
                currentTime < o.overlay_start_time_sec + o.overlay_duration_sec;

              return (
                <div
                  key={`tl-${o.matched_ad_id}-${o.overlay_start_time_sec}`}
                  title={`${o.matched_ad_id} — ${fmt(o.overlay_start_time_sec)} (score: ${o.score})`}
                  className={`absolute top-1 bottom-1 rounded-full transition-all cursor-pointer
                    ${isActive
                      ? "bg-indigo-400"
                      : "bg-indigo-700/60 hover:bg-indigo-600/80"}`}
                  style={{ left: `${left}%`, width: `${width}%` }}
                  onClick={() => seekTo(o.overlay_start_time_sec)}
                />
              );
            })}
            {/* Playhead */}
            <div
              className="absolute top-0 bottom-0 w-0.5 bg-white/80 pointer-events-none"
              style={{ left: `${(currentTime / totalDur) * 100}%` }}
            />
          </div>
        </div>
      )}

      {/* ── Ad list (click to seek) ────────────────────────────────────── */}
      {metadata.overlays.length > 0 && (
        <section className="w-full max-w-5xl">
          <h2 className="text-sm font-medium text-gray-400 mb-2">
            삽입 결정된 광고 목록 ({metadata.overlays.length}개)
            <span className="ml-2 text-xs text-gray-600 font-normal">
              광고 이름 클릭 → 해당 시점으로 이동
            </span>
          </h2>
          <div className="overflow-x-auto rounded-xl border border-gray-800">
            <table className="w-full text-xs text-gray-300">
              <thead>
                <tr className="bg-gray-800/60 text-gray-400">
                  <th className="text-left px-4 py-2">광고 ID</th>
                  <th className="text-left px-4 py-2">타입</th>
                  <th className="text-right px-4 py-2">시작</th>
                  <th className="text-right px-4 py-2">길이(s)</th>
                  <th className="text-right px-4 py-2">좌표 (x,y,w,h)</th>
                  <th className="text-right px-4 py-2">점수</th>
                </tr>
              </thead>
              <tbody>
                {metadata.overlays.map((o) => {
                  const isActive =
                    currentTime >= o.overlay_start_time_sec &&
                    currentTime < o.overlay_start_time_sec + o.overlay_duration_sec;
                  return (
                    <tr
                      key={`row-${o.matched_ad_id}-${o.overlay_start_time_sec}`}
                      className={`border-t border-gray-800 transition-colors ${
                        isActive ? "bg-indigo-950/50" : "hover:bg-gray-800/30"
                      }`}
                    >
                      {/* Ad name — click to seek */}
                      <td
                        className="px-4 py-2 font-mono text-indigo-300 truncate max-w-[200px]
                                   cursor-pointer hover:text-indigo-100 hover:underline select-none"
                        title={`클릭 → ${fmt(o.overlay_start_time_sec)}으로 이동`}
                        onClick={() => seekTo(o.overlay_start_time_sec)}
                      >
                        {isActive && (
                          <span className="inline-block w-1.5 h-1.5 rounded-full bg-indigo-400 mr-1.5 mb-0.5 align-middle" />
                        )}
                        {o.matched_ad_id}
                      </td>
                      <td className="px-4 py-2">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                            o.ad_type === "video_clip"
                              ? "bg-violet-900/60 text-violet-300"
                              : "bg-teal-900/60 text-teal-300"
                          }`}
                        >
                          {o.ad_type}
                        </span>
                      </td>
                      <td
                        className="px-4 py-2 text-right cursor-pointer hover:text-white select-none"
                        onClick={() => seekTo(o.overlay_start_time_sec)}
                      >
                        {fmt(o.overlay_start_time_sec)}
                      </td>
                      <td className="px-4 py-2 text-right">
                        {o.overlay_duration_sec.toFixed(1)}
                      </td>
                      <td className="px-4 py-2 text-right font-mono text-gray-500">
                        {o.coordinates_x},{o.coordinates_y},{o.coordinates_w},{o.coordinates_h}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <span
                          className={`font-bold ${
                            o.score >= 60
                              ? "text-green-400"
                              : o.score >= 30
                                ? "text-yellow-400"
                                : "text-gray-500"
                          }`}
                        >
                          {o.score}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
