"use client";

import { useState, useEffect, useRef, FormEvent } from "react";
import { useRouter } from "next/navigation";

interface VodFile  { name: string; path: string; }
interface CompletedJob { job_id: string; filename: string; updated_at: string; }

function cleanTitle(filename: string): string {
  return filename
    .replace(/\.(mp4|avi|mkv|mov|wmv)$/i, "")
    .replace(/-광고 narrative수정본.*$/i, "")
    .replace(/\(\s*재혁\s*\)/gi, "")
    .replace(/[-_]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

const CARD_GRADIENTS = [
  "linear-gradient(160deg, #1B3A5C 0%, #0D1F36 100%)",
  "linear-gradient(160deg, #3B1A1A 0%, #1F0D0D 100%)",
  "linear-gradient(160deg, #1A3B1A 0%, #0D1F0D 100%)",
  "linear-gradient(160deg, #3B2A1A 0%, #1F150D 100%)",
  "linear-gradient(160deg, #2A1A3B 0%, #150D1F 100%)",
  "linear-gradient(160deg, #1A2A3B 0%, #0D151F 100%)",
];
function cardGrad(id: string) { return CARD_GRADIENTS[id.charCodeAt(0) % CARD_GRADIENTS.length]; }

/* 섹션별 페이지 제목 매핑 */
const SECTION_TITLE_MAP: Record<string, string> = {
  home:     "홈",
  movies:   "영화/해외드라마",
  fastvod:  "FAST VOD",
  tv:       "TV방송",
  anime:    "애니/다큐",
};

/* 상단 서비스 탭 (제철장터 제거) */
const TOP_TABS = [
  { label: "아이들나라", icon: null },
  { label: "Disney+",  color: "#1464F6", bold: true },
  { label: "NETFLIX",  color: "#E50914", bold: true },
  { label: "YouTube",  color: "#FF0000", bold: true },
  { label: "OTT/앱",  icon: null },
  { label: "LG헬로비전 돌아보기", icon: null },
];

export default function HomePage() {
  const router = useRouter();

  const [completedJobs, setCompletedJobs] = useState<CompletedJob[]>([]);
  const [loadingJobs, setLoadingJobs]     = useState(true);
  const [activeTab, setActiveTab]         = useState("OTT/앱");

  /* 편성표 hover 추적 */
  const [scheduleHoverIdx, setScheduleHoverIdx] = useState<number>(0);
  /* 미리보기 비디오 URL */
  const [previewVideoUrl, setPreviewVideoUrl] = useState<string | null>(null);
  const previewVideoRef = useRef<HTMLVideoElement>(null);
  const videoUrlCache = useRef<Record<string, string>>({});
  /* 투표 오버레이 */
  const [showVoteOverlay, setShowVoteOverlay] = useState(false);
  const [selectedVoteType, setSelectedVoteType] = useState<string | null>(null);
  const [voteSubmitted, setVoteSubmitted] = useState(false);

  /* localStorage에서 시청 진행률 읽기 */
  const [watchProgress, setWatchProgress] = useState<Record<string, { percent: number }>>({});
  useEffect(() => {
    const loadProgress = () => {
      try {
        const data = JSON.parse(localStorage.getItem("vod_watch_progress") || "{}");
        setWatchProgress(data);
      } catch { /* ignore */ }
    };
    loadProgress();
    // 플레이어에서 돌아올 때 (탭 포커스 복귀 시) 다시 읽기
    const handleVisibility = () => { if (document.visibilityState === "visible") loadProgress(); };
    document.addEventListener("visibilitychange", handleVisibility);
    window.addEventListener("focus", loadProgress);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibility);
      window.removeEventListener("focus", loadProgress);
    };
  }, []);

  /* 리스트 섹션 스크롤 컨테이너 */
  const listScrollRef = useRef<HTMLDivElement>(null);
  const recoScrollRef = useRef<HTMLDivElement>(null);

  /* URL 섹션 파라미터 → 페이지 제목 */
  const [pageTitle, setPageTitle] = useState("영화/해외드라마");
  useEffect(() => {
    if (typeof window !== "undefined") {
      const section = new URLSearchParams(window.location.search).get("section") ?? "movies";
      setPageTitle(SECTION_TITLE_MAP[section] ?? "영화/해외드라마");
    }
  }, []);

  /* 관리자 */
  const [showAdmin, setShowAdmin]       = useState(false);
  const [vodFiles, setVodFiles]         = useState<VodFile[]>([]);
  const [selectedPath, setSelectedPath] = useState("");
  const [submitting, setSubmitting]     = useState(false);
  const [submitResult, setSubmitResult] = useState<{ job_id: string } | null>(null);
  const [submitError, setSubmitError]   = useState<string | null>(null);

  function loadJobs() {
    setLoadingJobs(true);
    fetch("/api/backend/jobs/completed")
      .then(r => r.json())
      .then(d => setCompletedJobs(d.jobs ?? []))
      .catch(() => {})
      .finally(() => setLoadingJobs(false));
  }

  useEffect(() => {
    loadJobs();
    fetch("/api/backend/vod/files")
      .then(r => r.json())
      .then(d => setVodFiles(d.files ?? []))
      .catch(() => {});
  }, []);

  useEffect(() => { if (submitResult) { loadJobs(); setShowAdmin(false); } }, [submitResult]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!selectedPath) return;
    setSubmitError(null); setSubmitting(true);
    try {
      const res = await fetch("/api/backend/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_path: selectedPath }),
      });
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail ?? res.statusText); }
      setSubmitResult(await res.json());
    } catch (err: unknown) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally { setSubmitting(false); }
  }

  const listCards = completedJobs.slice(0);

  /* 편성표용 10개: 투표추천 5 + 시즌추천 5 */
  const scheduleItems = (() => {
    if (completedJobs.length === 0) return [];
    const reversed = [...completedJobs].reverse();
    const pool = [...completedJobs, ...reversed];
    return pool.slice(0, 10);
  })();

  /* 취향 저격 추천 카드 (하단 섹션용) */
  const recoCards = (() => {
    if (completedJobs.length === 0) return [];
    const reversed = [...completedJobs].reverse();
    const pool = [...reversed, ...completedJobs];
    return pool.slice(0, 10);
  })();

  /* 편성표 hover → 비디오 URL 가져오기 */
  useEffect(() => {
    const item = scheduleItems[scheduleHoverIdx];
    if (!item) return;
    const jobId = item.job_id;
    if (videoUrlCache.current[jobId]) {
      setPreviewVideoUrl(videoUrlCache.current[jobId]);
      return;
    }
    fetch(`/api/backend/overlay/${jobId}`)
      .then(r => r.json())
      .then(d => {
        if (d.original_video_url) {
          videoUrlCache.current[jobId] = d.original_video_url;
          setPreviewVideoUrl(d.original_video_url);
        }
      })
      .catch(() => {});
  }, [scheduleHoverIdx, scheduleItems]);

  /* 비디오 URL 변경 시 자동 재생 */
  useEffect(() => {
    const v = previewVideoRef.current;
    if (v && previewVideoUrl) {
      v.src = previewVideoUrl;
      v.load();
      v.play().catch(() => {});
    }
  }, [previewVideoUrl]);

  /* 투표용 컬렉션 타입 */
  const VOTE_COLLECTIONS = [
    {
      type: "A 타입",
      label: "A 컬렉션",
      desc: "액션/스릴러 중심 편성",
      color: "#E60012",
      items: scheduleItems.slice(0, 5),
    },
    {
      type: "B 타입",
      label: "B 컬렉션",
      desc: "드라마/로맨스 중심 편성",
      color: "#8B5CF6",
      items: [...scheduleItems].reverse().slice(0, 5),
    },
    {
      type: "C 타입",
      label: "C 컬렉션",
      desc: "예능/다큐 중심 편성",
      color: "#06B6D4",
      items: scheduleItems.slice(2, 7),
    },
  ];

  return (
    <div className="min-h-screen flex flex-col" style={{ background: "#0D0F18" }}>

      {/* ── 상단 서비스 탭 ─────────────────────────────────────────── */}
      <div
        className="flex items-center gap-6 px-6 py-2 flex-shrink-0"
        style={{
          background: "#161B2C",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          height: 44,
        }}
      >
        {TOP_TABS.map(tab => (
          <button
            key={tab.label}
            onClick={() => { if (tab.label === "OTT/앱") setActiveTab(tab.label); }}
            className="text-sm font-semibold whitespace-nowrap transition-opacity hover:opacity-80"
            style={{
              color:   tab.color ?? (activeTab === tab.label ? "#FFFFFF" : "#8892A4"),
              opacity: tab.label === "LG헬로비전 돌아보기" ? 0.7 : 1,
            }}
          >
            {tab.label}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setShowAdmin(!showAdmin)}
            className="text-xs px-2 py-1 rounded"
            style={{ background: "#252D42", color: "#8892A4" }}
          >
            + 분석 추가
          </button>
        </div>
      </div>

      {/* ── 관리자 패널 ────────────────────────────────────────────── */}
      {showAdmin && (
        <div className="mx-6 mt-3 p-4 rounded-xl flex-shrink-0"
          style={{ background: "#1A2035", border: "1px solid rgba(255,255,255,0.08)" }}>
          <p className="text-xs font-semibold mb-3" style={{ color: "#8892A4" }}>영상 분석 작업 제출</p>
          <form onSubmit={handleSubmit} className="flex gap-3">
            <select
              className="flex-1 rounded-lg px-3 py-2 text-sm text-white focus:outline-none"
              style={{ background: "#252D42", border: "1px solid rgba(255,255,255,0.1)" }}
              value={selectedPath} onChange={e => setSelectedPath(e.target.value)} required
            >
              <option value="" disabled>— 영상 파일 선택 ({vodFiles.length}개) —</option>
              {vodFiles.map(f => <option key={f.path} value={f.path}>{f.name}</option>)}
            </select>
            <button type="submit" disabled={submitting || !selectedPath}
              className="px-5 py-2 rounded-lg text-sm font-semibold text-white disabled:opacity-50 hover:opacity-90 transition-opacity"
              style={{ background: "#E60012" }}>
              {submitting ? "제출 중…" : "분석 시작"}
            </button>
          </form>
          {submitError  && <p className="mt-2 text-xs" style={{ color: "#f87171" }}>{submitError}</p>}
          {submitResult && <p className="mt-2 text-xs" style={{ color: "#4ade80" }}>✅ 분석 시작됨 · {submitResult.job_id}</p>}
        </div>
      )}

      {/* ── 메인 콘텐츠 ────────────────────────────────────────────── */}
      <div className="flex-1 px-6 pt-4 pb-6 overflow-y-auto">

        {/* 페이지 제목 (클릭한 사이드바 항목과 일치) */}
        <h2 className="text-xl font-bold text-white mb-4">
          {pageTitle}
          {pageTitle === "FAST VOD" && (
            <span className="ml-2 text-sm font-normal" style={{ color: "#8892A4" }}>
              · 무료 광고 지원 스트리밍
            </span>
          )}
        </h2>

        {/* ── 편성표 + 미리보기 섹션 ──────────────────────────────── */}
        {loadingJobs ? (
          <div className="flex gap-4 mb-6">
            <div className="animate-pulse rounded-xl" style={{ width: "50%", height: 280, background: "#1A2035" }} />
            <div className="animate-pulse rounded-xl flex-1" style={{ height: 280, background: "#1A2035" }} />
          </div>
        ) : completedJobs.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-2xl mb-6"
            style={{ height: 240, background: "#1A2035", border: "1px solid rgba(255,255,255,0.06)" }}>
            <p className="text-4xl mb-3">📺</p>
            <p className="text-sm" style={{ color: "#8892A4" }}>분석 완료된 콘텐츠가 없습니다</p>
            <button onClick={() => setShowAdmin(true)}
              className="mt-3 text-xs px-4 py-2 rounded-lg text-white"
              style={{ background: "#E60012" }}>
              + 영상 분석 시작
            </button>
          </div>
        ) : (
          <div className="flex gap-4 mb-6" style={{ height: 280 }}>

            {/* ── 왼쪽: 비디오 미리보기 (자동 재생) ── */}
            <div
              className="relative rounded-xl overflow-hidden cursor-pointer"
              style={{ width: "62%", background: "#000" }}
              onClick={() => {
                const item = scheduleItems[scheduleHoverIdx];
                if (item) router.push(`/player/${item.job_id}`);
              }}
            >
              {/* 비디오 요소 */}
              <video
                ref={previewVideoRef}
                muted
                playsInline
                className="absolute inset-0 w-full h-full object-cover"
                style={{ opacity: previewVideoUrl ? 1 : 0, transition: "opacity 0.4s ease" }}
              />

              {/* 비디오 없을 때 폴백 배경 */}
              {!previewVideoUrl && (
                <div className="absolute inset-0 opacity-20"
                  style={{ background: cardGrad(scheduleItems[scheduleHoverIdx]?.job_id ?? "x") }} />
              )}

              {/* 하단 그라데이션 + 정보 오버레이 */}
              <div className="absolute inset-0 flex flex-col justify-end"
                style={{ background: "linear-gradient(to top, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0.2) 40%, transparent 70%)" }}>
                <div className="p-5">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-[10px] font-bold px-2 py-0.5 rounded"
                      style={{ background: "#E60012", color: "#fff" }}>FAST VOD</span>
                    <span className="text-[10px] font-bold px-2 py-0.5 rounded"
                      style={{ background: "rgba(255,255,255,0.15)", color: "#ccc" }}>무료</span>
                    {scheduleHoverIdx < 5 ? (
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded"
                        style={{ background: "#8B5CF6", color: "#fff" }}>투표추천</span>
                    ) : (
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded"
                        style={{ background: "#06B6D4", color: "#fff" }}>시즌추천</span>
                    )}
                  </div>
                  <h3 className="text-lg font-black text-white leading-tight"
                    style={{ textShadow: "0 2px 8px rgba(0,0,0,0.8)" }}>
                    {scheduleItems[scheduleHoverIdx]
                      ? cleanTitle(scheduleItems[scheduleHoverIdx].filename)
                      : "콘텐츠 선택"}
                  </h3>
                </div>
              </div>
            </div>

            {/* ── 오른쪽: 2주차 편성표 (태그 그리드 스타일) ── */}
            <div
              className="flex-1 rounded-xl overflow-hidden flex flex-col"
              style={{ background: "#161B2C", border: "1px solid rgba(255,255,255,0.08)" }}
            >
              {/* 편성표 헤더 */}
              <div className="flex items-center px-4 py-2.5"
                style={{ background: "#1A2035", borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
                <span className="text-sm font-bold" style={{ color: "#FFFFFF" }}>2주차 무료 VOD 편성표</span>
              </div>

              {/* 태그 그리드 편성표 */}
              <div className="flex-1 p-3 overflow-y-auto" style={{ scrollbarWidth: "thin" }}>
                {/* 투표추천 행들 */}
                {(() => {
                  const voteItems = scheduleItems.slice(0, 5);
                  const seasonItems = scheduleItems.slice(5, 10);
                  // 5개를 2줄로 나눔 (3 + 2)
                  const voteRow1 = voteItems.slice(0, 3);
                  const voteRow2 = voteItems.slice(3, 5);
                  const seasonRow1 = seasonItems.slice(0, 3);
                  const seasonRow2 = seasonItems.slice(3, 5);

                  const renderTag = (job: CompletedJob, globalIdx: number) => {
                    const isActive = scheduleHoverIdx === globalIdx;
                    const isVote = globalIdx < 5;
                    return (
                      <button
                        key={`tag-${job.job_id}-${globalIdx}`}
                        onMouseEnter={() => setScheduleHoverIdx(globalIdx)}
                        onClick={() => router.push(`/player/${job.job_id}`)}
                        className="px-3 py-2 rounded-lg text-xs font-medium truncate transition-all cursor-pointer"
                        style={{
                          background: isActive
                            ? (isVote ? "#8B5CF6" : "#06B6D4")
                            : "#252D42",
                          color: isActive ? "#fff" : "#B0B8C8",
                          border: isActive
                            ? `1px solid ${isVote ? "#A78BFA" : "#67E8F9"}`
                            : "1px solid rgba(255,255,255,0.06)",
                          maxWidth: 160,
                        }}
                      >
                        {cleanTitle(job.filename)}
                      </button>
                    );
                  };

                  return (
                    <div className="flex flex-col gap-2">
                      {/* 투표추천 줄 */}
                      <div className="flex flex-wrap gap-2">
                        {voteRow1.map((job, i) => renderTag(job, i))}
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {voteRow2.map((job, i) => renderTag(job, i + 3))}
                      </div>
                      {/* 구분선 */}
                      <div style={{ height: 1, background: "rgba(255,255,255,0.06)", margin: "4px 0" }} />
                      {/* 시즌추천 줄 */}
                      <div className="flex flex-wrap gap-2">
                        {seasonRow1.map((job, i) => renderTag(job, i + 5))}
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {seasonRow2.map((job, i) => renderTag(job, i + 8))}
                      </div>
                    </div>
                  );
                })()}
              </div>

              {/* 하단: 투표하기 버튼 */}
              <div
                className="px-4 py-2.5 flex items-center justify-between"
                style={{ background: "#1A2035", borderTop: "1px solid rgba(255,255,255,0.08)" }}
              >
                <span className="text-xs font-medium" style={{ color: "#A0AABC" }}>
                  3주차 보고 싶은 컬렉션은?
                </span>
                <button
                  onClick={() => { setShowVoteOverlay(true); setSelectedVoteType(null); setVoteSubmitted(false); }}
                  className="text-xs font-bold px-4 py-1.5 rounded-lg transition-opacity hover:opacity-90"
                  style={{ background: "#E60012", color: "#fff" }}
                >
                  투표하기
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ── 투표 오버레이 ────────────────────────────────────────── */}
        {showVoteOverlay && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center"
            style={{ background: "rgba(0,0,0,0.75)", backdropFilter: "blur(8px)" }}
            onClick={() => setShowVoteOverlay(false)}
          >
            <div
              className="rounded-2xl overflow-hidden w-full"
              style={{ maxWidth: 860, background: "#161B2C", border: "1px solid rgba(255,255,255,0.1)" }}
              onClick={e => e.stopPropagation()}
            >
              {/* 오버레이 헤더 */}
              <div className="flex items-center justify-between px-6 py-4"
                style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
                <h3 className="text-lg font-bold text-white">보고 싶은 컬렉션은?</h3>
                <button
                  onClick={() => setShowVoteOverlay(false)}
                  className="w-8 h-8 rounded-full flex items-center justify-center transition-colors"
                  style={{ background: "rgba(255,255,255,0.08)", color: "#8892A4" }}
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              {/* 오버레이 바디: 3개 컬렉션 */}
              <div className="flex gap-4 p-6">
                {VOTE_COLLECTIONS.map(col => (
                  <div
                    key={col.type}
                    className="flex-1 rounded-xl overflow-hidden cursor-pointer transition-all"
                    style={{
                      background: "#1A2035",
                      border: selectedVoteType === col.type
                        ? `2px solid ${col.color}`
                        : "2px solid rgba(255,255,255,0.06)",
                      transform: selectedVoteType === col.type ? "scale(1.02)" : "scale(1)",
                      boxShadow: selectedVoteType === col.type
                        ? `0 0 20px ${col.color}33`
                        : "none",
                    }}
                    onClick={() => setSelectedVoteType(col.type)}
                  >
                    {/* 컬렉션 헤더 */}
                    <div className="px-4 py-3 flex items-center justify-between"
                      style={{ background: `${col.color}22`, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                      <span className="text-sm font-bold" style={{ color: col.color }}>{col.label}</span>
                      <span className="text-[10px]" style={{ color: "#8892A4" }}>{col.desc}</span>
                    </div>

                    {/* 컬렉션 아이템 목록 */}
                    <div className="p-3 flex flex-col gap-1.5">
                      {col.items.map((job, jIdx) => (
                        <div
                          key={`vote-${col.type}-${job.job_id}-${jIdx}`}
                          className="flex items-center gap-2 px-3 py-2 rounded-lg"
                          style={{ background: "rgba(255,255,255,0.04)" }}
                        >
                          <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: col.color }} />
                          <span className="text-xs text-white truncate">{cleanTitle(job.filename)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}

                {/* 오른쪽: 타입 선택 버튼 */}
                <div className="flex flex-col gap-3 justify-center" style={{ minWidth: 100 }}>
                  {VOTE_COLLECTIONS.map(col => (
                    <button
                      key={`btn-${col.type}`}
                      onClick={() => setSelectedVoteType(col.type)}
                      className="px-4 py-3 rounded-xl text-sm font-bold transition-all"
                      style={{
                        background: selectedVoteType === col.type ? col.color : "#252D42",
                        color: selectedVoteType === col.type ? "#fff" : "#8892A4",
                        border: selectedVoteType === col.type
                          ? `2px solid ${col.color}`
                          : "2px solid rgba(255,255,255,0.08)",
                      }}
                    >
                      {col.type}
                    </button>
                  ))}

                  {/* 확인 버튼 */}
                  <button
                    onClick={() => {
                      if (selectedVoteType) {
                        setVoteSubmitted(true);
                        setTimeout(() => setShowVoteOverlay(false), 1500);
                      }
                    }}
                    disabled={!selectedVoteType || voteSubmitted}
                    className="px-4 py-3 rounded-xl text-sm font-bold transition-all mt-2 disabled:opacity-40"
                    style={{
                      background: voteSubmitted ? "#22C55E" : "#E60012",
                      color: "#fff",
                    }}
                  >
                    {voteSubmitted ? "투표 완료!" : "확인"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── 콘텐츠 목록 섹션 ─────────────────────────────────────── */}
        {!loadingJobs && listCards.length > 0 && (
          <div>
            {/* 섹션 헤더 */}
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-bold text-white">
                금주의 인기 TOP 10 무료 VOD
              </h3>
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-bold" style={{ color: "#E60012" }}>01</span>
                  <span className="text-xs" style={{ color: "#8892A4" }}>
                    / {Math.min(listCards.length, 10).toString().padStart(2, "0")}
                  </span>
                </div>
                {/* 스크롤 화살표 */}
                <div className="flex gap-1">
                  <button
                    onClick={() => listScrollRef.current?.scrollBy({ left: -580, behavior: "smooth" })}
                    className="w-7 h-7 rounded-full flex items-center justify-center transition-colors"
                    style={{ background: "rgba(255,255,255,0.08)", color: "#888" }}
                    onMouseEnter={e => { e.currentTarget.style.background = "rgba(255,255,255,0.2)"; e.currentTarget.style.color = "#fff"; }}
                    onMouseLeave={e => { e.currentTarget.style.background = "rgba(255,255,255,0.08)"; e.currentTarget.style.color = "#888"; }}
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
                    </svg>
                  </button>
                  <button
                    onClick={() => listScrollRef.current?.scrollBy({ left: 580, behavior: "smooth" })}
                    className="w-7 h-7 rounded-full flex items-center justify-center transition-colors"
                    style={{ background: "rgba(255,255,255,0.08)", color: "#888" }}
                    onMouseEnter={e => { e.currentTarget.style.background = "rgba(255,255,255,0.2)"; e.currentTarget.style.color = "#fff"; }}
                    onMouseLeave={e => { e.currentTarget.style.background = "rgba(255,255,255,0.08)"; e.currentTarget.style.color = "#888"; }}
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                    </svg>
                  </button>
                </div>
              </div>
            </div>

            {/* 가로 스크롤 카드 */}
            <div
              ref={listScrollRef}
              className="flex gap-3 py-5"
              style={{
                overflowX: "auto",
                overflowY: "visible",
                scrollbarWidth: "none",
                margin: "-20px 0",
                padding: "20px 0",
              }}
            >
              {listCards.slice(0, 10).map((job, idx) => (
                <button
                  key={job.job_id}
                  onClick={() => router.push(`/player/${job.job_id}`)}
                  className="group relative rounded-xl overflow-hidden flex-shrink-0 text-left"
                  style={{
                    width: 265,
                    height: 260,
                    background: cardGrad(job.job_id),
                    transition: "transform 0.3s cubic-bezier(0.25, 0.46, 0.45, 0.94), outline 0.2s ease, box-shadow 0.3s ease",
                    outline: "3px solid transparent",
                    outlineOffset: "-3px",
                    zIndex: 1,
                  }}
                  onMouseEnter={e => {
                    const el = e.currentTarget as HTMLElement;
                    el.style.transform = "scale(1.08)";
                    el.style.outline = "3px solid #FFFFFF";
                    el.style.boxShadow = "0 12px 36px rgba(0,0,0,0.6)";
                    el.style.zIndex = "20";

                    /* 자동 스크롤: 카드가 화면 오른쪽/왼쪽 가장자리에 가까우면 스크롤 */
                    const container = listScrollRef.current;
                    if (container) {
                      const cardRight = el.offsetLeft + el.offsetWidth;
                      const visibleRight = container.scrollLeft + container.clientWidth;
                      const cardLeft = el.offsetLeft;
                      const visibleLeft = container.scrollLeft;

                      if (cardRight > visibleRight - 80) {
                        /* 오른쪽 끝 근처 → 다음 카드들 보이도록 스크롤 */
                        container.scrollBy({ left: 280, behavior: "smooth" });
                      } else if (cardLeft < visibleLeft + 80) {
                        /* 왼쪽 끝 근처 → 이전 카드들 보이도록 스크롤 */
                        container.scrollBy({ left: -280, behavior: "smooth" });
                      }
                    }
                  }}
                  onMouseLeave={e => {
                    const el = e.currentTarget as HTMLElement;
                    el.style.transform = "scale(1)";
                    el.style.outline = "3px solid transparent";
                    el.style.boxShadow = "none";
                    el.style.zIndex = "1";
                  }}
                >
                  {/* 배경 장식 */}
                  <div className="absolute inset-0 opacity-15"
                    style={{ background: "radial-gradient(circle at 70% 40%, rgba(255,255,255,0.12) 0%, transparent 55%)" }} />

                  {/* 배지 */}
                  <div className="absolute top-2.5 left-2.5 flex gap-1.5 z-10">
                    <span className="text-[10px] font-bold px-2 py-0.5 rounded"
                      style={{ background: "#E60012", color: "#fff" }}>
                      무료
                    </span>
                    {idx % 3 === 0 && (
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded"
                        style={{ background: "#F59E0B", color: "#000" }}>
                        AI매칭
                      </span>
                    )}
                  </div>

                  {/* 제목 */}
                  <div className="absolute bottom-0 left-0 right-0 px-3 py-2.5"
                    style={{ background: "linear-gradient(to top, rgba(0,0,0,0.9) 0%, transparent 100%)", paddingBottom: 12 }}>
                    <p className="text-xs font-semibold text-white leading-tight line-clamp-2">
                      {cleanTitle(job.filename)}
                    </p>
                  </div>

                  {/* 시청 진행률 바 */}
                  {(() => {
                    const prog = watchProgress[job.job_id];
                    const pct = prog ? Math.min(prog.percent, 100) : 0;
                    return pct > 0 ? (
                      <div className="absolute bottom-0 left-0 right-0" style={{ height: 3, background: "rgba(255,255,255,0.15)", zIndex: 10 }}>
                        <div style={{
                          height: "100%",
                          width: `${pct}%`,
                          background: "#E60012",
                          borderRadius: "0 2px 2px 0",
                        }} />
                      </div>
                    ) : null;
                  })()}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* ── 취향 저격 무료 VOD 섹션 ──────────────────────────────── */}
        {!loadingJobs && recoCards.length > 0 && (
          <div className="mt-6">
            {/* 섹션 헤더 */}
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-bold text-white">
                취향 저격 무료 VOD
              </h3>
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-bold" style={{ color: "#E60012" }}>01</span>
                  <span className="text-xs" style={{ color: "#8892A4" }}>
                    / {Math.min(recoCards.length, 10).toString().padStart(2, "0")}
                  </span>
                </div>
                {/* 스크롤 화살표 */}
                <div className="flex gap-1">
                  <button
                    onClick={() => recoScrollRef.current?.scrollBy({ left: -580, behavior: "smooth" })}
                    className="w-7 h-7 rounded-full flex items-center justify-center transition-colors"
                    style={{ background: "rgba(255,255,255,0.08)", color: "#888" }}
                    onMouseEnter={e => { e.currentTarget.style.background = "rgba(255,255,255,0.2)"; e.currentTarget.style.color = "#fff"; }}
                    onMouseLeave={e => { e.currentTarget.style.background = "rgba(255,255,255,0.08)"; e.currentTarget.style.color = "#888"; }}
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
                    </svg>
                  </button>
                  <button
                    onClick={() => recoScrollRef.current?.scrollBy({ left: 580, behavior: "smooth" })}
                    className="w-7 h-7 rounded-full flex items-center justify-center transition-colors"
                    style={{ background: "rgba(255,255,255,0.08)", color: "#888" }}
                    onMouseEnter={e => { e.currentTarget.style.background = "rgba(255,255,255,0.2)"; e.currentTarget.style.color = "#fff"; }}
                    onMouseLeave={e => { e.currentTarget.style.background = "rgba(255,255,255,0.08)"; e.currentTarget.style.color = "#888"; }}
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                    </svg>
                  </button>
                </div>
              </div>
            </div>

            {/* 가로 스크롤 카드 */}
            <div
              ref={recoScrollRef}
              className="flex gap-3 py-5"
              style={{
                overflowX: "auto",
                overflowY: "visible",
                scrollbarWidth: "none",
                margin: "-20px 0",
                padding: "20px 0",
              }}
            >
              {recoCards.slice(0, 10).map((job, idx) => (
                <button
                  key={`reco-${job.job_id}-${idx}`}
                  onClick={() => router.push(`/player/${job.job_id}`)}
                  className="group relative rounded-xl overflow-hidden flex-shrink-0 text-left"
                  style={{
                    width: 265,
                    height: 260,
                    background: cardGrad(job.job_id),
                    transition: "transform 0.3s cubic-bezier(0.25, 0.46, 0.45, 0.94), outline 0.2s ease, box-shadow 0.3s ease",
                    outline: "3px solid transparent",
                    outlineOffset: "-3px",
                    zIndex: 1,
                  }}
                  onMouseEnter={e => {
                    const el = e.currentTarget as HTMLElement;
                    el.style.transform = "scale(1.08)";
                    el.style.outline = "3px solid #FFFFFF";
                    el.style.boxShadow = "0 12px 36px rgba(0,0,0,0.6)";
                    el.style.zIndex = "20";
                    const container = recoScrollRef.current;
                    if (container) {
                      const cardRight = el.offsetLeft + el.offsetWidth;
                      const visibleRight = container.scrollLeft + container.clientWidth;
                      const cardLeft = el.offsetLeft;
                      const visibleLeft = container.scrollLeft;
                      if (cardRight > visibleRight - 80) {
                        container.scrollBy({ left: 280, behavior: "smooth" });
                      } else if (cardLeft < visibleLeft + 80) {
                        container.scrollBy({ left: -280, behavior: "smooth" });
                      }
                    }
                  }}
                  onMouseLeave={e => {
                    const el = e.currentTarget as HTMLElement;
                    el.style.transform = "scale(1)";
                    el.style.outline = "3px solid transparent";
                    el.style.boxShadow = "none";
                    el.style.zIndex = "1";
                  }}
                >
                  {/* 배경 장식 */}
                  <div className="absolute inset-0 opacity-15"
                    style={{ background: "radial-gradient(circle at 70% 40%, rgba(255,255,255,0.12) 0%, transparent 55%)" }} />

                  {/* 배지: 앞 5개는 투표, 뒤 5개는 시즌추천 */}
                  <div className="absolute top-2.5 left-2.5 flex gap-1.5 z-10">
                    <span className="text-[10px] font-bold px-2 py-0.5 rounded"
                      style={{ background: "#E60012", color: "#fff" }}>
                      무료
                    </span>
                    {idx < 5 ? (
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded"
                        style={{ background: "#8B5CF6", color: "#fff" }}>
                        투표추천
                      </span>
                    ) : (
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded"
                        style={{ background: "#06B6D4", color: "#fff" }}>
                        시즌추천
                      </span>
                    )}
                  </div>

                  {/* 제목 */}
                  <div className="absolute bottom-0 left-0 right-0 px-3 py-2.5"
                    style={{ background: "linear-gradient(to top, rgba(0,0,0,0.9) 0%, transparent 100%)", paddingBottom: 12 }}>
                    <p className="text-xs font-semibold text-white leading-tight line-clamp-2">
                      {cleanTitle(job.filename)}
                    </p>
                  </div>

                  {/* 시청 진행률 바 */}
                  {(() => {
                    const prog = watchProgress[job.job_id];
                    const pct = prog ? Math.min(prog.percent, 100) : 0;
                    return pct > 0 ? (
                      <div className="absolute bottom-0 left-0 right-0" style={{ height: 3, background: "rgba(255,255,255,0.15)", zIndex: 10 }}>
                        <div style={{
                          height: "100%",
                          width: `${pct}%`,
                          background: "#E60012",
                          borderRadius: "0 2px 2px 0",
                        }} />
                      </div>
                    ) : null;
                  })()}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
