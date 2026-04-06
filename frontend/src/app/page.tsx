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

  /* 히어로 카드 hover 추적 */
  const [hoveredHeroId, setHoveredHeroId] = useState<string | null>(null);
  /* 캐러셀 슬라이딩 윈도우 시작 인덱스 */
  const [heroStartIdx, setHeroStartIdx] = useState(0);

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

  /* 히어로 영역: 슬라이딩 윈도우 캐러셀 */
  const HERO_VISIBLE = 5;
  const safeStart = Math.min(heroStartIdx, Math.max(0, completedJobs.length - HERO_VISIBLE));
  const heroCards = completedJobs.slice(safeStart, safeStart + HERO_VISIBLE);
  const peekCard = completedJobs.length > safeStart + HERO_VISIBLE
    ? completedJobs[safeStart + HERO_VISIBLE] : null;
  const lastHeroId = heroCards.length > 0 ? heroCards[heroCards.length - 1].job_id : null;
  const shouldPeek = !!(peekCard && (
    hoveredHeroId === lastHeroId ||
    hoveredHeroId === peekCard.job_id
  ));
  const listCards = completedJobs.slice(0);

  /* peek 카드에 hover → 윈도우를 1칸 전진 (무한 캐러셀) */
  const handlePeekHover = () => {
    if (!peekCard) return;
    const nextStart = safeStart + 1;
    if (nextStart + HERO_VISIBLE <= completedJobs.length) {
      setHeroStartIdx(nextStart);
      /* 새 윈도우의 마지막 카드(= 현재 peek 카드)를 hover 상태로 유지 */
      setHoveredHeroId(peekCard.job_id);
    }
  };

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

        {/* ── 히어로 섹션 (hover → 확대/축소) ────────────────────── */}
        {loadingJobs ? (
          <div className="flex gap-3 mb-6">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="animate-pulse rounded-xl flex-1" style={{ height: 240, background: "#1A2035" }} />
            ))}
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
          /* 외부 래퍼: peek 카드 + 메인 카드를 하나의 hover 영역으로 묶음 */
          <div
            className="relative mb-6"
            style={{ height: 260, overflow: "hidden" }}
            onMouseLeave={() => {
              setHoveredHeroId(null);
              setHeroStartIdx(0);
            }}
          >
            {/* ── 메인 히어로 카드 (flex 레이아웃, 항상 100% 폭) ── */}
            <div
              className="flex gap-3"
              style={{
                height: 260,
                /* peek 카드 공간 확보: shouldPeek일 때 오른쪽 패딩 추가 */
                paddingRight: shouldPeek ? 162 : 0,
                transition: "padding-right 0.4s cubic-bezier(0.25, 0.46, 0.45, 0.94)",
              }}
            >
              {heroCards.map((job, idx) => {
                const isLast      = idx === heroCards.length - 1;
                const isHovered   = hoveredHeroId === job.job_id ||
                                    (isLast && shouldPeek);
                const isFirst     = idx === 0;
                const someHovered = hoveredHeroId !== null;

                /* flex 비율 계산 */
                let flexVal = 1;
                if (isHovered) {
                  flexVal = 3.5;
                } else if (!someHovered && isFirst) {
                  flexVal = 2.8;
                }

                return (
                  <button
                    key={job.job_id}
                    onClick={() => router.push(`/player/${job.job_id}`)}
                    onMouseEnter={() => setHoveredHeroId(job.job_id)}
                    className="relative rounded-xl overflow-hidden text-left"
                    style={{
                      flex: flexVal,
                      minWidth: 0,
                      background: cardGrad(job.job_id),
                      transition: "flex 0.45s cubic-bezier(0.25, 0.46, 0.45, 0.94), box-shadow 0.3s ease, outline 0.2s ease",
                      outline: isHovered ? "3px solid #FFFFFF" : "3px solid transparent",
                      outlineOffset: "-3px",
                      borderRadius: 12,
                      boxShadow: isHovered
                        ? "0 12px 36px rgba(0,0,0,0.6)"
                        : "none",
                    }}
                  >
                    {/* 배경 장식 */}
                    <div className="absolute inset-0 opacity-20"
                      style={{ background: "radial-gradient(circle at 70% 50%, rgba(255,255,255,0.15) 0%, transparent 60%)" }} />

                    {/* ─── 확대 상태: 콘텐츠 정보 오버레이 ─── */}
                    <div
                      className="absolute inset-0 flex flex-col justify-between p-5"
                      style={{
                        opacity: (isHovered || (!someHovered && isFirst)) ? 1 : 0,
                        transition: "opacity 0.35s ease",
                        pointerEvents: "none",
                      }}
                    >
                      <div>
                        <p className="text-xs font-medium mb-2" style={{ color: "#A0AABC" }}>
                          금주 인기 콘텐츠
                        </p>
                        <h3
                          className="font-black text-white leading-tight mb-2"
                          style={{
                            fontSize: isHovered ? "1.25rem" : (!someHovered && isFirst ? "1.25rem" : "0.85rem"),
                            textShadow: "0 2px 8px rgba(0,0,0,0.5)",
                            transition: "font-size 0.3s ease",
                          }}
                        >
                          {cleanTitle(job.filename)}
                        </h3>
                        <p
                          className="text-xs leading-relaxed"
                          style={{
                            color: "#8892A4",
                            opacity: isHovered ? 1 : (!someHovered && isFirst ? 1 : 0),
                            transition: "opacity 0.3s ease",
                          }}
                        >
                          AI 맥락 분석 기반 광고 오버레이<br />무료로 즐기는 FAST VOD 콘텐츠
                        </p>
                      </div>
                      <div
                        className="flex items-center gap-2"
                        style={{
                          opacity: isHovered ? 1 : (!someHovered && isFirst ? 1 : 0),
                          transition: "opacity 0.3s ease",
                        }}
                      >
                        <span className="text-xs font-bold px-2 py-0.5 rounded"
                          style={{ background: "#E60012", color: "#fff" }}>FAST VOD</span>
                        <span className="text-xs px-2 py-0.5 rounded"
                          style={{ background: "rgba(255,255,255,0.12)", color: "#ccc" }}>무료</span>
                        <span className="text-xs px-2 py-0.5 rounded"
                          style={{ background: "rgba(255,255,255,0.12)", color: "#ccc" }}>AI 광고</span>
                      </div>
                    </div>

                    {/* ─── 축소 상태: 하단 제목만 표시 ─── */}
                    <div
                      className="absolute bottom-0 left-0 right-0 px-3 py-2.5"
                      style={{
                        background: "linear-gradient(to top, rgba(0,0,0,0.85) 0%, transparent 100%)",
                        opacity: (!isHovered && (someHovered || !isFirst)) ? 1 : 0,
                        transition: "opacity 0.3s ease",
                        pointerEvents: "none",
                      }}
                    >
                      <p className="text-xs font-bold text-white leading-tight line-clamp-2">
                        {cleanTitle(job.filename)}
                      </p>
                    </div>
                  </button>
                );
              })}

              {/* 빈 슬롯 (카드가 5개 미만일 때) */}
              {heroCards.length < HERO_VISIBLE && [...Array(HERO_VISIBLE - heroCards.length)].map((_, i) => (
                <div key={`empty-${i}`} className="flex-1 rounded-xl"
                  style={{ background: "#1A2035", border: "1px dashed rgba(255,255,255,0.08)" }} />
              ))}
            </div>

            {/* ─── Peek 카드: position absolute → 레이아웃 흔들림 없음 ─── */}
            {peekCard && (
              <button
                key={`peek-${peekCard.job_id}`}
                onClick={() => router.push(`/player/${peekCard.job_id}`)}
                onMouseEnter={handlePeekHover}
                className="rounded-xl overflow-hidden text-left"
                style={{
                  position: "absolute",
                  top: 0,
                  right: 0,
                  width: 150,
                  height: 260,
                  opacity: shouldPeek ? 0.85 : 0,
                  pointerEvents: shouldPeek ? "auto" : "none",
                  background: cardGrad(peekCard.job_id),
                  transition: "opacity 0.4s ease",
                  zIndex: 2,
                }}
              >
                {/* 배경 장식 */}
                <div className="absolute inset-0 opacity-10"
                  style={{ background: "radial-gradient(circle at 50% 30%, rgba(255,255,255,0.2) 0%, transparent 60%)" }} />

                {/* 하단 제목 */}
                <div
                  className="absolute bottom-0 left-0 right-0 px-3 py-2.5"
                  style={{
                    background: "linear-gradient(to top, rgba(0,0,0,0.85) 0%, transparent 100%)",
                    pointerEvents: "none",
                  }}
                >
                  <p className="text-xs font-bold text-white leading-tight line-clamp-2">
                    {cleanTitle(peekCard.filename)}
                  </p>
                </div>
              </button>
            )}
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
      </div>
    </div>
  );
}
