"use client";

import { useState, useEffect, FormEvent } from "react";
import { useRouter } from "next/navigation";

interface VodFile {
  name: string;
  path: string;
}

interface CompletedJob {
  job_id: string;
  filename: string;
  updated_at: string;
}

const CATEGORIES = ["전체", "드라마", "예능", "영화", "다큐"];

/** 파일명에서 표시용 제목 추출 */
function cleanTitle(filename: string): string {
  return filename
    .replace(/\.(mp4|avi|mkv|mov|wmv)$/i, "")
    .replace(/-광고 narrative수정본.*$/i, "")
    .replace(/[-_]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** job_id 첫 글자 기반 카드 그라디언트 */
function cardGradient(jobId: string): string {
  const palettes = [
    "linear-gradient(135deg,#1a0608 0%,#2d1010 60%,#1a1a1a 100%)",
    "linear-gradient(135deg,#06101a 0%,#0d2535 60%,#1a1a1a 100%)",
    "linear-gradient(135deg,#0a1a06 0%,#162d10 60%,#1a1a1a 100%)",
    "linear-gradient(135deg,#1a1506 0%,#2d2510 60%,#1a1a1a 100%)",
    "linear-gradient(135deg,#10061a 0%,#1e0d35 60%,#1a1a1a 100%)",
  ];
  return palettes[jobId.charCodeAt(0) % palettes.length];
}

export default function HomePage() {
  const router = useRouter();
  const [activeCategory, setActiveCategory] = useState("전체");

  const [completedJobs, setCompletedJobs] = useState<CompletedJob[]>([]);
  const [loadingJobs, setLoadingJobs]     = useState(true);

  const [showAdmin, setShowAdmin]         = useState(false);
  const [vodFiles, setVodFiles]           = useState<VodFile[]>([]);
  const [selectedPath, setSelectedPath]   = useState("");
  const [submitting, setSubmitting]       = useState(false);
  const [submitResult, setSubmitResult]   = useState<{ job_id: string } | null>(null);
  const [submitError, setSubmitError]     = useState<string | null>(null);

  function loadCompletedJobs() {
    setLoadingJobs(true);
    fetch("/api/backend/jobs/completed")
      .then((r) => r.json())
      .then((data) => setCompletedJobs(data.jobs ?? []))
      .catch(() => {})
      .finally(() => setLoadingJobs(false));
  }

  useEffect(() => {
    loadCompletedJobs();
    fetch("/api/backend/vod/files")
      .then((r) => r.json())
      .then((data) => setVodFiles(data.files ?? []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (submitResult) {
      loadCompletedJobs();
      setShowAdmin(false);
    }
  }, [submitResult]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!selectedPath) return;
    setSubmitError(null);
    setSubmitting(true);
    try {
      const res = await fetch("/api/backend/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_path: selectedPath }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail ?? res.statusText);
      }
      const data = await res.json();
      setSubmitResult(data);
    } catch (err: unknown) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  const featuredJob = completedJobs[0] ?? null;

  return (
    <div className="min-h-screen" style={{ background: "#0F0F0F" }}>

      {/* ── 상단 카테고리 탭 ──────────────────────────────────── */}
      <div
        className="sticky top-0 z-40 flex items-center gap-6 px-8 py-3"
        style={{
          background:     "rgba(15,15,15,0.96)",
          backdropFilter: "blur(10px)",
          borderBottom:   "1px solid rgba(255,255,255,0.06)",
        }}
      >
        <div className="flex items-center gap-1.5 mr-4 select-none">
          <span className="font-black text-sm" style={{ color: "#E60012" }}>LG</span>
          <span className="font-semibold text-sm text-white">헬로비전</span>
        </div>

        {CATEGORIES.map((cat) => (
          <button
            key={cat}
            onClick={() => setActiveCategory(cat)}
            className="text-sm font-medium transition-colors py-1 border-b-2"
            style={{
              color:       activeCategory === cat ? "#FFFFFF" : "#666666",
              borderColor: activeCategory === cat ? "#E60012" : "transparent",
            }}
          >
            {cat}
          </button>
        ))}

        <div className="ml-auto flex items-center gap-3">
          {submitResult && (
            <button
              onClick={() => router.push(`/player/${submitResult.job_id}`)}
              className="text-xs px-3 py-1.5 rounded-lg text-white"
              style={{ background: "#E60012" }}
            >
              분석 완료 → 재생
            </button>
          )}
          <button
            onClick={() => { setShowAdmin(!showAdmin); setSubmitResult(null); }}
            className="text-xs px-3 py-1.5 rounded-lg transition-colors"
            style={{ background: "#1E1E1E", color: "#777777" }}
          >
            + 분석 추가
          </button>
          <button
            onClick={loadCompletedJobs}
            disabled={loadingJobs}
            className="text-xs disabled:opacity-40"
            style={{ color: "#444444" }}
          >
            ↻
          </button>
        </div>
      </div>

      {/* ── 관리자 패널 (숨김) ──────────────────────────────── */}
      {showAdmin && (
        <div
          className="mx-8 mt-4 p-5 rounded-xl"
          style={{ background: "#1A1A1A", border: "1px solid rgba(255,255,255,0.08)" }}
        >
          <p className="text-xs font-semibold mb-3" style={{ color: "#AAAAAA" }}>
            영상 분석 작업 제출
          </p>
          <form onSubmit={handleSubmit} className="flex gap-3">
            <select
              className="flex-1 rounded-lg px-3 py-2 text-sm text-white focus:outline-none"
              style={{ background: "#252525", border: "1px solid rgba(255,255,255,0.1)" }}
              value={selectedPath}
              onChange={(e) => setSelectedPath(e.target.value)}
              required
            >
              <option value="" disabled>— 영상 파일 선택 ({vodFiles.length}개) —</option>
              {vodFiles.map((f) => (
                <option key={f.path} value={f.path}>{f.name}</option>
              ))}
            </select>
            <button
              type="submit"
              disabled={submitting || !selectedPath}
              className="px-5 py-2 rounded-lg text-sm font-semibold text-white disabled:opacity-50 hover:opacity-90 transition-opacity"
              style={{ background: "#E60012" }}
            >
              {submitting ? "제출 중…" : "분석 시작"}
            </button>
          </form>
          {submitError && (
            <p className="mt-2 text-xs" style={{ color: "#f87171" }}>{submitError}</p>
          )}
          {submitResult && (
            <p className="mt-2 text-xs" style={{ color: "#4ade80" }}>
              ✅ 분석 시작됨 · Job ID: {submitResult.job_id}
            </p>
          )}
        </div>
      )}

      {/* ── 히어로 배너 ─────────────────────────────────────── */}
      {featuredJob && (
        <div
          className="relative mx-8 mt-6 rounded-2xl overflow-hidden cursor-pointer group"
          style={{ height: 300 }}
          onClick={() => router.push(`/player/${featuredJob.job_id}`)}
        >
          <div
            className="absolute inset-0 transition-opacity group-hover:opacity-90"
            style={{ background: cardGradient(featuredJob.job_id) }}
          />
          <div className="absolute -right-16 -top-16 w-64 h-64 rounded-full opacity-10"
            style={{ background: "#E60012" }} />
          <div className="absolute -right-8 -bottom-8 w-40 h-40 rounded-full opacity-5"
            style={{ background: "#E60012" }} />

          <div className="absolute inset-0 flex flex-col justify-end p-8">
            <div className="flex items-center gap-2 mb-3">
              <span className="text-xs font-bold px-2 py-0.5 rounded"
                style={{ background: "#E60012", color: "#fff" }}>
                FAST VOD
              </span>
              <span className="text-xs font-medium px-2 py-0.5 rounded"
                style={{ background: "rgba(255,255,255,0.1)", color: "#ccc" }}>
                무료
              </span>
              <span className="text-xs font-medium px-2 py-0.5 rounded"
                style={{ background: "rgba(255,255,255,0.1)", color: "#ccc" }}>
                AI 광고 매칭
              </span>
            </div>
            <h2 className="text-3xl font-black text-white mb-2 leading-tight">
              {cleanTitle(featuredJob.filename)}
            </h2>
            <p className="text-sm mb-5" style={{ color: "#999999" }}>
              맥락 기반 AI 광고 오버레이 · LG 헬로비전 FAST VOD
            </p>
            <div className="flex gap-3" onClick={(e) => e.stopPropagation()}>
              <button
                onClick={() => router.push(`/player/${featuredJob.job_id}`)}
                className="flex items-center gap-2 px-7 py-2.5 rounded-xl font-semibold text-sm text-white hover:opacity-85 transition-opacity"
                style={{ background: "#E60012" }}
              >
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M8 5v14l11-7z" />
                </svg>
                지금 보기
              </button>
              <button
                className="flex items-center gap-2 px-7 py-2.5 rounded-xl font-semibold text-sm"
                style={{ background: "rgba(255,255,255,0.1)", color: "#fff" }}
              >
                상세 정보
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── 무료 VOD 콘텐츠 그리드 ─────────────────────────── */}
      <div className="px-8 mt-8 pb-14">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-bold text-white">
            무료 VOD
            <span className="ml-2 text-sm font-normal" style={{ color: "#E60012" }}>
              · 광고 지원
            </span>
          </h3>
          <span className="text-xs" style={{ color: "#555555" }}>
            {completedJobs.length}개 콘텐츠
          </span>
        </div>

        {loadingJobs && (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="rounded-xl animate-pulse"
                style={{ height: 180, background: "#1E1E1E" }} />
            ))}
          </div>
        )}

        {!loadingJobs && completedJobs.length === 0 && (
          <div className="flex flex-col items-center justify-center py-24 gap-3">
            <svg className="w-14 h-14 opacity-20" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1}
                d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
            <p className="text-sm" style={{ color: "#555555" }}>분석 완료된 콘텐츠가 없습니다</p>
            <button onClick={() => setShowAdmin(true)}
              className="text-xs px-4 py-2 rounded-lg mt-1 text-white"
              style={{ background: "#E60012" }}>
              + 영상 분석 시작
            </button>
          </div>
        )}

        {!loadingJobs && completedJobs.length > 0 && (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
            {completedJobs.map((job, idx) => (
              <ContentCard
                key={job.job_id}
                job={job}
                isFeatured={idx === 0}
                onPlay={() => router.push(`/player/${job.job_id}`)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ── 콘텐츠 카드 ──────────────────────────────────────────── */
function ContentCard({
  job,
  isFeatured,
  onPlay,
}: {
  job: CompletedJob;
  isFeatured: boolean;
  onPlay: () => void;
}) {
  const title = cleanTitle(job.filename);

  return (
    <button onClick={onPlay} className="hv-card group text-left w-full">
      {/* 썸네일 */}
      <div className="relative flex items-center justify-center"
        style={{ height: 130, background: cardGradient(job.job_id) }}>
        <div
          className="w-10 h-10 rounded-full flex items-center justify-center transition-all
                     opacity-0 group-hover:opacity-100 scale-90 group-hover:scale-100"
          style={{ background: "rgba(230,0,18,0.9)" }}
        >
          <svg className="w-5 h-5 text-white ml-0.5" fill="currentColor" viewBox="0 0 24 24">
            <path d="M8 5v14l11-7z" />
          </svg>
        </div>
        <div className="absolute top-2 left-2 flex gap-1">
          {isFeatured && (
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded"
              style={{ background: "#E60012", color: "#fff" }}>추천</span>
          )}
          <span className="text-[9px] font-bold px-1.5 py-0.5 rounded"
            style={{ background: "rgba(0,0,0,0.65)", color: "#fff" }}>무료</span>
        </div>
        <div className="absolute top-2 right-2">
          <span className="text-[9px] font-medium px-1.5 py-0.5 rounded"
            style={{ background: "rgba(230,0,18,0.25)", color: "#E60012", border: "1px solid rgba(230,0,18,0.4)" }}>
            AI
          </span>
        </div>
        <div className="absolute bottom-0 left-0 right-0 h-0.5"
          style={{ background: "rgba(255,255,255,0.06)" }} />
      </div>

      {/* 카드 정보 */}
      <div className="p-3">
        <p className="text-white text-xs font-semibold leading-snug line-clamp-2 mb-1">{title}</p>
        <p className="text-[10px]" style={{ color: "#555555" }}>FAST VOD · 광고 포함</p>
      </div>
    </button>
  );
}
