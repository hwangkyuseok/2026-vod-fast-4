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

export default function HomePage() {
  const router = useRouter();

  // ── VOD 파일 목록 ──────────────────────────────────────────────────────────
  const [vodFiles, setVodFiles]         = useState<VodFile[]>([]);
  const [vodDir, setVodDir]             = useState<string>("");
  const [loadingFiles, setLoadingFiles]  = useState(true);
  const [filesError, setFilesError]     = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/backend/vod/files")
      .then((r) => r.json())
      .then((data) => {
        setVodFiles(data.files ?? []);
        setVodDir(data.vod_dir ?? "");
      })
      .catch(() => setFilesError("VOD 파일 목록을 불러오지 못했습니다."))
      .finally(() => setLoadingFiles(false));
  }, []);

  // ── Job 제출 ───────────────────────────────────────────────────────────────
  const [selectedPath, setSelectedPath] = useState("");
  const [submitting, setSubmitting]     = useState(false);
  const [error, setError]               = useState<string | null>(null);
  const [submitResult, setSubmitResult] = useState<{
    job_id: string;
    status: string;
  } | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!selectedPath) return;
    setError(null);
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
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  // ── 완료된 Job 목록 ────────────────────────────────────────────────────────
  const [completedJobs, setCompletedJobs]       = useState<CompletedJob[]>([]);
  const [loadingJobs, setLoadingJobs]            = useState(true);
  const [jobsError, setJobsError]               = useState<string | null>(null);
  const [selectedJobId, setSelectedJobId]       = useState("");

  function loadCompletedJobs() {
    setLoadingJobs(true);
    setJobsError(null);
    fetch("/api/backend/jobs/completed")
      .then((r) => r.json())
      .then((data) => setCompletedJobs(data.jobs ?? []))
      .catch(() => setJobsError("완료된 작업 목록을 불러오지 못했습니다."))
      .finally(() => setLoadingJobs(false));
  }

  useEffect(() => {
    loadCompletedJobs();
  }, []);

  // 새 분석 제출 후 목록 자동 갱신
  useEffect(() => {
    if (submitResult) loadCompletedJobs();
  }, [submitResult]);

  function formatDate(iso: string) {
    const d = new Date(iso);
    return d.toLocaleString("ko-KR", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit",
    });
  }

  return (
    <main className="flex flex-col items-center justify-center min-h-screen gap-10 p-8">
      <h1 className="text-4xl font-bold text-white tracking-tight">
        VOD <span className="text-indigo-400">Ad Overlay</span> System
      </h1>

      {/* ── 영상 분석 작업 제출 ─────────────────────────────────────────────── */}
      <section className="w-full max-w-xl bg-gray-900 rounded-2xl p-6 shadow-xl border border-gray-800">
        <h2 className="text-lg font-semibold mb-4 text-gray-200">
          1. 영상 분석 작업 제출
        </h2>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          {loadingFiles ? (
            <div className="text-sm text-gray-500 py-2">VOD 파일 목록 로딩 중…</div>
          ) : filesError ? (
            <div className="text-sm text-red-400 bg-red-950/40 rounded-lg px-3 py-2">
              {filesError}
            </div>
          ) : vodFiles.length === 0 ? (
            <div className="text-sm text-yellow-400 bg-yellow-950/40 rounded-lg px-3 py-2">
              {vodDir || "VOD"} 디렉토리에 영상 파일이 없습니다.
            </div>
          ) : (
            <select
              className="w-full rounded-lg bg-gray-800 border border-gray-700 px-4 py-2.5
                         text-sm text-gray-200 focus:outline-none focus:ring-2
                         focus:ring-indigo-500 cursor-pointer"
              value={selectedPath}
              onChange={(e) => setSelectedPath(e.target.value)}
              required
            >
              <option value="" disabled>
                — 영상 파일 선택 ({vodFiles.length}개) —
              </option>
              {vodFiles.map((f) => (
                <option key={f.path} value={f.path}>
                  {f.name}
                </option>
              ))}
            </select>
          )}

          <button
            type="submit"
            disabled={submitting || !selectedPath}
            className="rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50
                       transition-colors py-2.5 text-sm font-medium"
          >
            {submitting ? "제출 중…" : "분석 시작"}
          </button>
        </form>

        {error && (
          <p className="mt-3 text-sm text-red-400 bg-red-950/40 rounded-lg px-3 py-2">
            {error}
          </p>
        )}

        {submitResult && (
          <div className="mt-3 text-sm bg-green-950/40 rounded-lg px-3 py-2 text-green-300">
            <p>✅ Job ID: <span className="font-mono">{submitResult.job_id}</span></p>
            <p className="text-gray-400 mt-1">
              분석이 완료되면 아래 Player에서 영상을 확인하세요.
            </p>
          </div>
        )}
      </section>

      {/* ── 분석 완료된 Job 재생 ─────────────────────────────────────────────── */}
      <section className="w-full max-w-xl bg-gray-900 rounded-2xl p-6 shadow-xl border border-gray-800">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-200">
            2. 분석 완료된 Job 재생
          </h2>
          <button
            onClick={loadCompletedJobs}
            disabled={loadingJobs}
            className="text-xs text-gray-400 hover:text-gray-200 transition-colors
                       disabled:opacity-40 flex items-center gap-1"
          >
            {loadingJobs ? "로딩 중…" : "↻ 새로고침"}
          </button>
        </div>

        {loadingJobs ? (
          <div className="text-sm text-gray-500 py-2">완료된 작업 로딩 중…</div>
        ) : jobsError ? (
          <div className="text-sm text-red-400 bg-red-950/40 rounded-lg px-3 py-2">
            {jobsError}
          </div>
        ) : completedJobs.length === 0 ? (
          <div className="text-sm text-yellow-400 bg-yellow-950/40 rounded-lg px-3 py-2">
            완료된 분석 작업이 없습니다.
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <select
              className="w-full rounded-lg bg-gray-800 border border-gray-700 px-4 py-2.5
                         text-sm text-gray-200 focus:outline-none focus:ring-2
                         focus:ring-violet-500 cursor-pointer"
              value={selectedJobId}
              onChange={(e) => setSelectedJobId(e.target.value)}
            >
              <option value="" disabled>
                — 완료된 작업 선택 ({completedJobs.length}개) —
              </option>
              {completedJobs.map((j) => (
                <option key={j.job_id} value={j.job_id}>
                  {j.filename} · {formatDate(j.updated_at)}
                </option>
              ))}
            </select>

            <button
              onClick={() => {
                if (selectedJobId) router.push(`/player/${selectedJobId}`);
              }}
              disabled={!selectedJobId}
              className="rounded-lg bg-violet-600 hover:bg-violet-500 disabled:opacity-40
                         transition-colors py-2.5 text-sm font-medium"
            >
              플레이어 열기
            </button>
          </div>
        )}

        {submitResult && (
          <button
            onClick={() => router.push(`/player/${submitResult.job_id}`)}
            className="mt-3 w-full rounded-lg border border-violet-700 text-violet-300
                       hover:bg-violet-900/30 transition-colors py-2 text-sm"
          >
            방금 제출한 작업 열기 →
          </button>
        )}
      </section>
    </main>
  );
}
