"use client";

import { useState, useEffect, useRef, useCallback, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

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

/* ── 썸네일 매칭: 영상 파일명 → public/ 폴더 이미지 ── */
const THUMBNAIL_FILES = [
  "나는 SOLO.E24.260304.720p NEXT.jpg",
  "무명전설.E02.260304.720p NEXT.jpg",
  "판사 이한영.E09.260130.720p NEXT.jpg",
  "언더커버 미쓰홍.E16.260308.720p NEXT.jpg",
  "스프링 피버.E07.260126.1080p.H264F1RST.jpg",
];
function findThumbnail(filename: string): string | null {
  // 영상 파일명에서 핵심 키워드 추출 (첫 번째 마침표 앞 = 타이틀)
  const title = filename.split(".")[0].replace(/[-_]/g, " ").trim().toLowerCase();
  for (const thumb of THUMBNAIL_FILES) {
    const thumbTitle = thumb.split(".")[0].replace(/[-_]/g, " ").trim().toLowerCase();
    if (title.includes(thumbTitle) || thumbTitle.includes(title)) {
      return `/${encodeURIComponent(thumb)}`;
    }
  }
  return null;
}

const SECTION_TITLE_MAP: Record<string, string> = {
  home:     "홈",
  movies:   "FAST VOD",
  fastvod:  "FAST VOD",
  tv:       "TV방송",
  anime:    "애니/다큐",
};

const TOP_TABS = [
  { label: "아이들나라", icon: null },
  { label: "Disney+",  color: "#1464F6", bold: true },
  { label: "NETFLIX",  color: "#E50914", bold: true },
  { label: "YouTube",  color: "#FF0000", bold: true },
  { label: "OTT/앱",  icon: null },
  { label: "LG헬로비전 돌아보기", icon: null },
];

/* ── 내비게이션 존 정의 ─────────────────────────────────────────── */
type FocusZone = "sidebar" | "schedule_outer" | "schedule" | "vote_btn" | "top10" | "reco";
// sidebar: 왼쪽 사이드바 포커싱
// schedule_outer: 편성표 외곽 포커싱 (Enter로 진입)
// schedule: 편성표 내부 2D 탐색 모드
const ZONE_ORDER: FocusZone[] = ["schedule_outer", "top10", "reco"];

/* 사이드바 아이템 (Sidebar.tsx의 NAV_ITEMS와 동기화) */
const SIDEBAR_ITEMS = ["마이메뉴","검색","전체메뉴","홈","영화/해외","FAST VOD","TV방송","애니/다큐","무제한관","설정"];
const SIDEBAR_SECTIONS: Record<number, string> = { 3: "home", 4: "movies", 5: "fastvod" };

export default function HomePage() {
  const router = useRouter();

  const [completedJobs, setCompletedJobs] = useState<CompletedJob[]>([]);
  const [loadingJobs, setLoadingJobs]     = useState(true);
  const [activeTab, setActiveTab]         = useState("OTT/앱");

  /* ── 리모컨 포커스 시스템 ── */
  const [focusZone, setFocusZone] = useState<FocusZone>("top10");
  const [focusIdx, setFocusIdx]   = useState(0);
  /* 사이드바 진입 전 존/인덱스 기억 (오른쪽으로 복귀할 때 사용) */
  const sidebarReturnRef = useRef<{ zone: FocusZone; idx: number }>({ zone: "top10", idx: 0 });

  /* 편성표 2D 그리드 레이아웃: [행][열] → 글로벌 인덱스 */
  const SCHEDULE_GRID = [
    [0, 1, 2],   // 투표추천 1행
    [3, 4],      // 투표추천 2행
    [5, 6, 7],   // 시즌추천 1행
    [8, 9],      // 시즌추천 2행
  ];

  /* 글로벌 인덱스 → (행, 열) 역매핑 */
  const idxToGrid = useCallback((idx: number) => {
    for (let r = 0; r < SCHEDULE_GRID.length; r++) {
      const c = SCHEDULE_GRID[r].indexOf(idx);
      if (c !== -1) return { row: r, col: c };
    }
    return { row: 0, col: 0 };
  }, []);

  /* 존별 DOM ref (스크롤 연동용) */
  const scheduleRef = useRef<HTMLDivElement>(null);
  const voteBtnRef  = useRef<HTMLDivElement>(null);
  const top10Ref    = useRef<HTMLDivElement>(null);
  const recoRef     = useRef<HTMLDivElement>(null);

  /* 편성표 선택 인덱스 (미리보기 연동) */
  const [scheduleHoverIdx, setScheduleHoverIdx] = useState<number>(0);

  /* 미리보기 비디오 URL */
  const [previewVideoUrl, setPreviewVideoUrl] = useState<string | null>(null);
  const previewVideoRef = useRef<HTMLVideoElement>(null);
  const videoUrlCache = useRef<Record<string, string>>({});

  /* 투표 오버레이 */
  const [showVoteOverlay, setShowVoteOverlay] = useState(false);
  const [selectedVoteType, setSelectedVoteType] = useState<string | null>(null);
  const [voteSubmitted, setVoteSubmitted] = useState(false);
  const [voteFocusIdx, setVoteFocusIdx] = useState(0); // 0=A, 1=B, 2=C, 3=확인

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
    const handleVisibility = () => { if (document.visibilityState === "visible") loadProgress(); };
    document.addEventListener("visibilitychange", handleVisibility);
    window.addEventListener("focus", loadProgress);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibility);
      window.removeEventListener("focus", loadProgress);
    };
  }, []);

  /* 스크롤 컨테이너 */
  const listScrollRef = useRef<HTMLDivElement>(null);
  const recoScrollRef = useRef<HTMLDivElement>(null);

  /* URL 섹션 파라미터 → 페이지 제목 */
  const [pageTitle, setPageTitle] = useState("FAST VOD");
  useEffect(() => {
    if (typeof window !== "undefined") {
      const section = new URLSearchParams(window.location.search).get("section") ?? "movies";
      setPageTitle(SECTION_TITLE_MAP[section] ?? "FAST VOD");
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

  /* 편성표용 10개 */
  const scheduleItems = (() => {
    if (completedJobs.length === 0) return [];
    const reversed = [...completedJobs].reverse();
    const pool = [...completedJobs, ...reversed];
    return pool.slice(0, 10);
  })();

  /* 취향 저격 추천 카드 */
  const recoCards = (() => {
    if (completedJobs.length === 0) return [];
    const reversed = [...completedJobs].reverse();
    const pool = [...reversed, ...completedJobs];
    return pool.slice(0, 10);
  })();

  /* 편성표 포커스 → 미리보기 연동 */
  useEffect(() => {
    if (focusZone === "schedule") {
      setScheduleHoverIdx(focusIdx);
    }
  }, [focusZone, focusIdx]);

  /* 편성표 선택 → 비디오 URL 가져오기 */
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

  /* ── 존별 최대 아이템 수 ── */
  const zoneMaxIdx = useCallback((zone: FocusZone) => {
    switch (zone) {
      case "schedule_outer": return 0;
      case "schedule": return Math.max(0, scheduleItems.length - 1);
      case "vote_btn": return 0;
      case "top10":    return Math.max(0, Math.min(listCards.length, 10) - 1);
      case "reco":     return Math.max(0, Math.min(recoCards.length, 10) - 1);
      default: return 0;
    }
  }, [scheduleItems.length, listCards.length, recoCards.length]);

  /* ── 존 변경 / 포커스 이동 시 화면 스크롤 연동 ── */
  useEffect(() => {
    // 1) 편성표 진입 시 → 스크롤 제일 상단 (즉시)
    if (focusZone === "schedule_outer") {
      window.scrollTo({ top: 0, behavior: "instant" });
    } else if (focusZone === "schedule") {
      window.scrollTo({ top: 0, behavior: "instant" });
      // 스케줄 컨테이너 스크롤 지원
      const el = document.getElementById(`schedule-tag-${focusIdx}`);
      el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } else {
      // 다른 존은 해당 섹션으로 스크롤
      const zoneRefMap: Record<string, React.RefObject<HTMLDivElement | null>> = {
        vote_btn: voteBtnRef,
        top10:    top10Ref,
        reco:     recoRef,
      };
      const ref = zoneRefMap[focusZone];
      ref?.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    // 카드 존은 개별 카드도 중앙 정렬
    if (focusZone === "top10" && listScrollRef.current) {
      const card = listScrollRef.current.children[focusIdx] as HTMLElement | undefined;
      card?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    }
    if (focusZone === "reco" && recoScrollRef.current) {
      const card = recoScrollRef.current.children[focusIdx] as HTMLElement | undefined;
      card?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    }
  }, [focusZone, focusIdx, idxToGrid]);

  /* ── 리모컨 키보드 핸들러 ── */
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    // 투표 오버레이 모드
    if (showVoteOverlay) {
      e.preventDefault();
      switch (e.key) {
        case "ArrowLeft":
          setVoteFocusIdx(p => Math.max(0, p - 1));
          break;
        case "ArrowRight":
          setVoteFocusIdx(p => Math.min(3, p + 1));
          break;
        case "ArrowUp":
          setVoteFocusIdx(p => Math.max(0, p - 1));
          break;
        case "ArrowDown":
          setVoteFocusIdx(p => Math.min(3, p + 1));
          break;
        case "Enter":
          if (voteFocusIdx < 3) {
            setSelectedVoteType(VOTE_COLLECTIONS[voteFocusIdx].type);
          } else {
            if (selectedVoteType) {
              setVoteSubmitted(true);
              setTimeout(() => setShowVoteOverlay(false), 1500);
            }
          }
          break;
        case "Escape":
        case "Backspace":
          setShowVoteOverlay(false);
          break;
      }
      return;
    }

    if (showAdmin) return;

    const { key } = e;
    if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter", "Escape", "Backspace"].includes(key)) return;
    e.preventDefault();

    /* ── 사이드바 모드 ── */
    if (focusZone === "sidebar") {
      switch (key) {
        case "ArrowUp":
          setFocusIdx(p => Math.max(0, p - 1));
          break;
        case "ArrowDown":
          setFocusIdx(p => Math.min(SIDEBAR_ITEMS.length - 1, p + 1));
          break;
        case "ArrowRight":
        case "Escape":
        case "Backspace":
          // 사이드바에서 나가기 → 이전 존으로 복귀
          setFocusZone(sidebarReturnRef.current.zone);
          setFocusIdx(sidebarReturnRef.current.idx);
          break;
        case "Enter": {
          const section = SIDEBAR_SECTIONS[focusIdx];
          if (section) {
            router.push(`/?section=${section}`);
          }
          // 사이드바에서 나가기
          if (section === "fastvod") {
            setFocusZone("top10");
            setFocusIdx(0);
          } else {
            setFocusZone(sidebarReturnRef.current.zone);
            setFocusIdx(sidebarReturnRef.current.idx);
          }
          break;
        }
      }
      return;
    }

    /* ── 편성표 내부 모드 (Enter로 진입한 상태) ── */
    if (focusZone === "schedule") {
      switch (key) {
        case "ArrowLeft":
        case "ArrowRight":
          // 선형 리스트에서는 좌우 이동 무시
          break;
        case "ArrowUp":
          if (focusIdx > 0) setFocusIdx(focusIdx - 1);
          break;
        case "ArrowDown":
          if (focusIdx < scheduleItems.length - 1) {
            setFocusIdx(focusIdx + 1);
          } else {
            // 마지막 아이템에서 밑으로 내리면 투표 버튼으로
            setFocusZone("vote_btn");
            setFocusIdx(0);
          }
          break;
        case "Enter": {
          const item = scheduleItems[focusIdx];
          if (item) router.push(`/player/${item.job_id}`);
          break;
        }
        case "Escape":
        case "Backspace":
          // 편성표 내부에서 나가기 → 편성표 외곽으로 복귀
          setFocusZone("schedule_outer");
          setFocusIdx(0);
          break;
      }
      return;
    }

    /* ── 투표 버튼 존 ── */
    if (focusZone === "vote_btn") {
      switch (key) {
        case "ArrowUp":
          setFocusZone("schedule_outer");
          setFocusIdx(0);
          break;
        case "ArrowDown":
          setFocusZone("top10");
          setFocusIdx(0);
          break;
        case "Enter":
          setShowVoteOverlay(true);
          setSelectedVoteType(null);
          setVoteSubmitted(false);
          setVoteFocusIdx(0);
          break;
        case "Escape":
        case "Backspace":
          setFocusZone("schedule_outer");
          setFocusIdx(0);
          break;
      }
      return;
    }

    /* ── 편성표 외곽 / top10 / reco: 1D 좌우 + 상하 존 이동 ── */
    switch (key) {
      case "ArrowLeft":
        if (focusZone === "schedule_outer" || focusIdx === 0) {
          // 제일 왼쪽이거나 편성표 외곽 → 사이드바로 진입
          sidebarReturnRef.current = { zone: focusZone, idx: focusIdx };
          setFocusZone("sidebar");
          setFocusIdx(5); // FAST VOD 위치에 기본 포커싱
        } else {
          setFocusIdx(p => Math.max(0, p - 1));
        }
        break;
      case "ArrowRight":
        if (focusZone !== "schedule_outer") setFocusIdx(p => Math.min(zoneMaxIdx(focusZone), p + 1));
        break;
      case "ArrowUp": {
        if (focusZone === "top10") {
          setFocusZone("schedule_outer");
          setFocusIdx(0);
        } else if (focusZone === "reco") {
          setFocusZone("top10");
          setFocusIdx(p => Math.min(p, zoneMaxIdx("top10")));
        }
        break;
      }
      case "ArrowDown": {
        if (focusZone === "schedule_outer") {
          setFocusZone("vote_btn");
          setFocusIdx(0);
        } else if (focusZone === "top10") {
          setFocusZone("reco");
          setFocusIdx(0);
        }
        break;
      }
      case "Enter": {
        if (focusZone === "schedule_outer") {
          // 편성표 외곽에서 Enter → 편성표 내부 진입
          setFocusZone("schedule");
          setFocusIdx(0);
        } else if (focusZone === "top10") {
          const item = listCards[focusIdx];
          if (item) router.push(`/player/${item.job_id}`);
        } else if (focusZone === "reco") {
          const item = recoCards[focusIdx];
          if (item) router.push(`/player/${item.job_id}`);
        }
        break;
      }
    }
  }, [focusZone, focusIdx, showVoteOverlay, showAdmin, voteFocusIdx, selectedVoteType,
      scheduleItems, listCards, recoCards, zoneMaxIdx, router, VOTE_COLLECTIONS, idxToGrid, SCHEDULE_GRID]);

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  /* ── 포커스 스타일 헬퍼 ── */
  const isFocused = (zone: FocusZone, idx: number) => focusZone === zone && focusIdx === idx;

  return (
    <>
    <Sidebar focusedIndex={focusZone === "sidebar" ? focusIdx : null} />
    <div className="min-h-screen flex flex-col" style={{ background: "#0D0F18", marginLeft: 68 }}>

      {/* ── 리모컨 안내 ── */}
      <div className="fixed bottom-4 right-4 z-40 flex items-center gap-2 px-4 py-2 rounded-xl"
        style={{ background: "rgba(26,32,53,0.95)", border: "1px solid rgba(255,255,255,0.1)" }}>
        <div className="flex gap-1">
          <kbd className="px-1.5 py-0.5 rounded text-[10px] font-mono" style={{ background: "#252D42", color: "#8892A4" }}>
            <svg className="w-3 h-3 inline" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" /></svg>
          </kbd>
          <kbd className="px-1.5 py-0.5 rounded text-[10px] font-mono" style={{ background: "#252D42", color: "#8892A4" }}>
            <svg className="w-3 h-3 inline" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" /></svg>
          </kbd>
          <kbd className="px-1.5 py-0.5 rounded text-[10px] font-mono" style={{ background: "#252D42", color: "#8892A4" }}>
            <svg className="w-3 h-3 inline" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" /></svg>
          </kbd>
          <kbd className="px-1.5 py-0.5 rounded text-[10px] font-mono" style={{ background: "#252D42", color: "#8892A4" }}>
            <svg className="w-3 h-3 inline" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" /></svg>
          </kbd>
        </div>
        <span className="text-[10px]" style={{ color: "#8892A4" }}>이동</span>
        <kbd className="px-2 py-0.5 rounded text-[10px] font-mono" style={{ background: "#E60012", color: "#fff" }}>OK</kbd>
        <span className="text-[10px]" style={{ color: "#8892A4" }}>선택</span>
      </div>

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
            className="text-sm font-semibold whitespace-nowrap"
            style={{
              color: tab.color ?? (activeTab === tab.label ? "#FFFFFF" : "#8892A4"),
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
              className="px-5 py-2 rounded-lg text-sm font-semibold text-white disabled:opacity-50"
              style={{ background: "#E60012" }}>
              {submitting ? "제출 중…" : "분석 시작"}
            </button>
          </form>
          {submitError  && <p className="mt-2 text-xs" style={{ color: "#f87171" }}>{submitError}</p>}
          {submitResult && <p className="mt-2 text-xs" style={{ color: "#4ade80" }}>분석 시작됨 · {submitResult.job_id}</p>}
        </div>
      )}

      {/* ── 메인 콘텐츠 ────────────────────────────────────────────── */}
      <div className="flex-1 px-6 pt-4 pb-6 overflow-y-auto">

        {/* 페이지 제목 */}
        <h2 className="text-xl font-bold text-white mb-4">
          {pageTitle}
          {pageTitle === "FAST VOD" && (
            <span className="ml-2 text-sm font-normal" style={{ color: "#8892A4" }}>
              · 무료 VOD 스트리밍
            </span>
          )}
        </h2>

        {/* ── 편성표 + 미리보기 섹션 ──────────────────────────────── */}
        {loadingJobs ? (
          <div className="mb-6">
            <div className="animate-pulse rounded-xl" style={{ height: 220, background: "#1A2035" }} />
          </div>
        ) : completedJobs.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-2xl mb-6"
            style={{ height: 200, background: "#1A2035", border: "1px solid rgba(255,255,255,0.06)" }}>
            <p className="text-4xl mb-3">📺</p>
            <p className="text-sm" style={{ color: "#8892A4" }}>분석 완료된 콘텐츠가 없습니다</p>
            <button onClick={() => setShowAdmin(true)}
              className="mt-3 text-xs px-4 py-2 rounded-lg text-white"
              style={{ background: "#E60012" }}>
              + 영상 분석 시작
            </button>
          </div>
        ) : (
          <>
          <div className="mb-6 flex gap-4">
            {/* ── 왼쪽 60%: 예고 영상 미리보기 ── */}
            <div
              className="rounded-xl overflow-hidden flex flex-col justify-center items-center relative"
              style={{
                width: "60%",
                background: "#1A2035",
                border: "1px solid rgba(255,255,255,0.08)",
                height: "360px",
              }}
            >
              {previewVideoUrl ? (
                <video
                  ref={previewVideoRef}
                  className="w-full h-full object-cover absolute inset-0"
                  autoPlay
                  muted
                  loop
                  playsInline
                />
              ) : (
                <div className="text-sm text-center z-10" style={{ color: "#8892A4" }}>
                  <p className="text-2xl mb-2">🎬</p>
                  편성표에서 영상을 선택하면<br/>미리보기가 재생됩니다.
                </div>
              )}
            </div>

            {/* ── 오른쪽 40%: 편성표 & 투표하기 ── */}
            <div className="flex flex-col gap-4" style={{ width: "40%", height: "360px" }}>
              <div
                ref={scheduleRef}
                className="rounded-xl overflow-hidden flex flex-col"
                style={{
                  flex: 1,
                  background: "#161B2C",
                  border: (focusZone === "schedule_outer" || focusZone === "schedule")
                    ? "2px solid #FFFFFF"
                    : "1px solid rgba(255,255,255,0.08)",
                  transition: "border 0.2s ease",
                }}
              >
                {/* 편성표 헤더 */}
                <div className="flex items-center justify-between px-4 py-2.5"
                  style={{ background: "#1A2035", borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
                  <span className="text-sm font-bold" style={{ color: "#FFFFFF" }}>2주차 무료 VOD 편성표</span>
                  {focusZone === "schedule_outer" && (
                    <span className="text-[10px] px-2 py-0.5 rounded"
                      style={{ background: "rgba(255,255,255,0.15)", color: "#ccc" }}>
                      OK 누르면 편성표 진입
                    </span>
                  )}
                  {focusZone === "schedule" && (
                    <span className="text-[10px] px-2 py-0.5 rounded"
                      style={{ background: "rgba(255,255,255,0.15)", color: "#ccc" }}>
                      편성표 탐색 중 · ESC 나가기
                    </span>
                  )}
                </div>

                {/* 세로형 스크롤 편성표 */}
                <div className="p-2" style={{ flex: 1, overflowY: "auto" }}>
                  <div className="flex flex-col gap-1.5 pb-2">
                    {scheduleItems.map((job, idx) => {
                      const isInside = focusZone === "schedule";
                      const isActive = isInside && isFocused("schedule", idx);
                      const isVote = idx < 5;
                      const tag = (
                        <div
                          id={`schedule-tag-${idx}`}
                          key={`tag-${job.job_id}-${idx}`}
                          className="px-3 py-2 rounded-lg text-xs font-medium truncate transition-all flex items-center justify-between flex-shrink-0"
                          style={{
                            background: isActive
                              ? (isVote ? "#8B5CF6" : "#06B6D4")
                              : "#252D42",
                            color: isActive ? "#fff" : "#B0B8C8",
                            border: isActive
                              ? "2px solid #FFFFFF"
                              : "1px solid rgba(255,255,255,0.06)",
                          }}
                        >
                          <div className="flex gap-2 items-center truncate">
                            <span className="text-[10px] w-3" style={{ opacity: 0.6 }}>{idx + 1}</span>
                            <span className="truncate">{cleanTitle(job.filename)}</span>
                          </div>
                        </div>
                      );

                      if (idx === 5) {
                        return [
                          <div key="separator" style={{ height: 1, background: "rgba(255,255,255,0.06)", margin: "4px 0" }} />,
                          tag
                        ];
                      }
                      return tag;
                    })}
                  </div>
                </div>
              </div>

              {/* 하단: 투표하기 버튼 (우측 레이아웃 편입) */}
              <div className="rounded-xl overflow-hidden flex-shrink-0" style={{ border: (focusZone === "vote_btn") ? "2px solid #E60012" : "1px solid rgba(255,255,255,0.08)" }}>
                <div
                  ref={voteBtnRef}
                  className="px-5 py-3 flex items-center justify-between"
                  style={{
                    background: "#161B2C",
                  }}
                >
                  <span className="text-sm font-bold text-white">
                    3주차 보고 싶은 컬렉션은?
                  </span>
                  <div
                    className="text-sm font-bold px-4 py-1.5 rounded-lg"
                    style={{
                      background: focusZone === "vote_btn" ? "#E60012" : "#3A1015",
                      color: "#fff",
                      border: focusZone === "vote_btn"
                        ? "2px solid #FFFFFF"
                        : "2px solid transparent",
                      transition: "all 0.2s ease",
                    }}
                  >
                    투표하기
                  </div>
                </div>
              </div>
            </div>

          </div>
          </>
        )}

        {/* ── 투표 오버레이 ────────────────────────────────────────── */}
        {showVoteOverlay && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center"
            style={{ background: "rgba(0,0,0,0.75)", backdropFilter: "blur(8px)" }}
          >
            <div
              className="rounded-2xl overflow-hidden w-full"
              style={{ maxWidth: 860, background: "#161B2C", border: "1px solid rgba(255,255,255,0.1)" }}
            >
              {/* 오버레이 헤더 */}
              <div className="flex items-center justify-between px-6 py-4"
                style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
                <h3 className="text-lg font-bold text-white">보고 싶은 컬렉션은?</h3>
                <span className="text-xs" style={{ color: "#8892A4" }}>
                  ← → 선택 &nbsp; OK 확인 &nbsp; ESC 닫기
                </span>
              </div>

              {/* 오버레이 바디: 3개 컬렉션 + 확인 */}
              <div className="flex gap-4 p-6">
                {VOTE_COLLECTIONS.map((col, colIdx) => (
                  <div
                    key={col.type}
                    className="flex-1 rounded-xl overflow-hidden transition-all"
                    style={{
                      background: "#1A2035",
                      border: (voteFocusIdx === colIdx || selectedVoteType === col.type)
                        ? `2px solid ${col.color}`
                        : "2px solid rgba(255,255,255,0.06)",
                      transform: voteFocusIdx === colIdx ? "scale(1.03)" : "scale(1)",
                      boxShadow: voteFocusIdx === colIdx
                        ? `0 0 24px ${col.color}55`
                        : "none",
                    }}
                  >
                    <div className="px-4 py-3 flex items-center justify-between"
                      style={{ background: `${col.color}22`, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                      <span className="text-sm font-bold" style={{ color: col.color }}>{col.label}</span>
                      <span className="text-[10px]" style={{ color: "#8892A4" }}>{col.desc}</span>
                    </div>

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
                  {VOTE_COLLECTIONS.map((col, colIdx) => (
                    <div
                      key={`btn-${col.type}`}
                      className="px-4 py-3 rounded-xl text-sm font-bold text-center transition-all"
                      style={{
                        background: selectedVoteType === col.type ? col.color
                          : voteFocusIdx === colIdx ? "rgba(255,255,255,0.12)" : "#252D42",
                        color: selectedVoteType === col.type ? "#fff"
                          : voteFocusIdx === colIdx ? "#fff" : "#8892A4",
                        border: voteFocusIdx === colIdx
                          ? `2px solid ${col.color}`
                          : selectedVoteType === col.type
                            ? `2px solid ${col.color}`
                            : "2px solid rgba(255,255,255,0.08)",
                        boxShadow: voteFocusIdx === colIdx
                          ? `0 0 12px ${col.color}44` : "none",
                      }}
                    >
                      {col.type}
                    </div>
                  ))}

                  {/* 확인 버튼 */}
                  <div
                    className="px-4 py-3 rounded-xl text-sm font-bold text-center mt-2"
                    style={{
                      background: voteSubmitted ? "#22C55E"
                        : voteFocusIdx === 3 ? "#E60012" : "#252D42",
                      color: (voteFocusIdx === 3 || voteSubmitted) ? "#fff" : "#8892A4",
                      border: voteFocusIdx === 3
                        ? "2px solid #FF4D5E"
                        : "2px solid rgba(255,255,255,0.08)",
                      boxShadow: voteFocusIdx === 3
                        ? "0 0 16px rgba(230,0,18,0.5)" : "none",
                      opacity: (!selectedVoteType && !voteSubmitted) ? 0.4 : 1,
                    }}
                  >
                    {voteSubmitted ? "투표 완료!" : "확인"}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── 금주의 무료 VOD 섹션 ─────────────────────────────── */}
        {!loadingJobs && listCards.length > 0 && (
          <div ref={top10Ref}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-bold text-white">
                금주의 무료 VOD
              </h3>
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-bold" style={{ color: "#E60012" }}>
                    {focusZone === "top10" ? String(focusIdx + 1).padStart(2, "0") : "01"}
                  </span>
                  <span className="text-xs" style={{ color: "#8892A4" }}>
                    / {Math.min(listCards.length, 10).toString().padStart(2, "0")}
                  </span>
                </div>
              </div>
            </div>

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
              {listCards.slice(0, 10).map((job, idx) => {
                const thumb = findThumbnail(job.filename);
                return (
                <div
                  key={job.job_id}
                  className="group relative rounded-xl overflow-hidden flex-shrink-0 text-left"
                  style={{
                    width: 265,
                    height: 300,
                    background: thumb ? "#000" : cardGrad(job.job_id),
                    transition: "transform 0.3s ease, box-shadow 0.3s ease",
                    transform: isFocused("top10", idx) ? "scale(1.08)" : "scale(1)",
                    outline: isFocused("top10", idx) ? "3px solid #FFFFFF" : "3px solid transparent",
                    outlineOffset: "-3px",
                    boxShadow: isFocused("top10", idx) ? "0 8px 24px rgba(0,0,0,0.5)" : "none",
                    zIndex: isFocused("top10", idx) ? 20 : 1,
                  }}
                >
                  {thumb ? (
                    <img src={thumb} alt={cleanTitle(job.filename)}
                      className="absolute inset-0 w-full h-full object-cover" />
                  ) : (
                    <div className="absolute inset-0 opacity-15"
                      style={{ background: "radial-gradient(circle at 70% 40%, rgba(255,255,255,0.12) 0%, transparent 55%)" }} />
                  )}

                  <div className="absolute top-2.5 left-2.5 flex gap-1.5 z-10">
                    <span className="text-[10px] font-bold px-2 py-0.5 rounded"
                      style={{ background: "#E60012", color: "#fff" }}>
                      무료
                    </span>
                  </div>

                  <div className="absolute bottom-0 left-0 right-0 px-3 py-2.5"
                    style={{ background: "linear-gradient(to top, rgba(0,0,0,0.9) 0%, transparent 100%)", paddingBottom: 12 }}>
                    <p className="text-xs font-semibold text-white leading-tight line-clamp-2">
                      {cleanTitle(job.filename)}
                    </p>
                  </div>

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
                </div>
                );
              })}
            </div>
          </div>
        )}

        {/* ── 취향 저격 무료 VOD 섹션 ──────────────────────────────── */}
        {!loadingJobs && recoCards.length > 0 && (
          <div ref={recoRef} className="mt-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-bold text-white">
                취향 저격 무료 VOD
              </h3>
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-bold" style={{ color: "#E60012" }}>
                    {focusZone === "reco" ? String(focusIdx + 1).padStart(2, "0") : "01"}
                  </span>
                  <span className="text-xs" style={{ color: "#8892A4" }}>
                    / {Math.min(recoCards.length, 10).toString().padStart(2, "0")}
                  </span>
                </div>
              </div>
            </div>

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
              {recoCards.slice(0, 10).map((job, idx) => {
                const thumb = findThumbnail(job.filename);
                return (
                <div
                  key={`reco-${job.job_id}-${idx}`}
                  className="group relative rounded-xl overflow-hidden flex-shrink-0 text-left"
                  style={{
                    width: 265,
                    height: 300,
                    background: thumb ? "#000" : cardGrad(job.job_id),
                    transition: "transform 0.3s ease, box-shadow 0.3s ease",
                    transform: isFocused("reco", idx) ? "scale(1.08)" : "scale(1)",
                    outline: isFocused("reco", idx) ? "3px solid #FFFFFF" : "3px solid transparent",
                    outlineOffset: "-3px",
                    boxShadow: isFocused("reco", idx) ? "0 8px 24px rgba(0,0,0,0.5)" : "none",
                    zIndex: isFocused("reco", idx) ? 20 : 1,
                  }}
                >
                  {thumb ? (
                    <img src={thumb} alt={cleanTitle(job.filename)}
                      className="absolute inset-0 w-full h-full object-cover" />
                  ) : (
                    <div className="absolute inset-0 opacity-15"
                      style={{ background: "radial-gradient(circle at 70% 40%, rgba(255,255,255,0.12) 0%, transparent 55%)" }} />
                  )}

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

                  <div className="absolute bottom-0 left-0 right-0 px-3 py-2.5"
                    style={{ background: "linear-gradient(to top, rgba(0,0,0,0.9) 0%, transparent 100%)", paddingBottom: 12 }}>
                    <p className="text-xs font-semibold text-white leading-tight line-clamp-2">
                      {cleanTitle(job.filename)}
                    </p>
                  </div>

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
                </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
    </>
  );
}
