"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import VideoPlayer from "@/components/VideoPlayer";
import type { JobStatus, OverlayMetadata } from "@/types/overlay";

// Next.js rewrite 경유 (/api/backend/* → http://step5-api:8000/*)
const API = "/api/backend";
const POLL_INTERVAL_MS = 5_000;

type PageState =
  | { phase: "loading" }
  | { phase: "polling"; status: JobStatus }
  | { phase: "ready"; metadata: OverlayMetadata }
  | { phase: "error"; message: string };

export default function PlayerPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const router    = useRouter();
  const [state, setState] = useState<PageState>({ phase: "loading" });

  const fetchOverlay = useCallback(async () => {
    const res = await fetch(`${API}/overlay/${jobId}`);
    if (res.status === 200) {
      const data: OverlayMetadata = await res.json();
      setState({ phase: "ready", metadata: data });
      return true;
    }
    // 202 = job not complete yet; treat as not done
    return false;
  }, [jobId]);

  const fetchStatus = useCallback(async (): Promise<JobStatus | null> => {
    const res = await fetch(`${API}/jobs/${jobId}`);
    if (!res.ok) return null;
    return res.json();
  }, [jobId]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;

    async function poll() {
      // First try to get overlay (in case job is already done)
      const done = await fetchOverlay();
      if (done) return;

      // Job not done → fetch status and keep polling
      const status = await fetchStatus();
      if (!status) {
        setState({ phase: "error", message: "Job not found." });
        return;
      }
      if (status.status === "failed") {
        setState({
          phase: "error",
          message: status.error_message ?? "Pipeline failed.",
        });
        return;
      }
      setState({ phase: "polling", status });
      timer = setTimeout(poll, POLL_INTERVAL_MS);
    }

    poll().catch((err) =>
      setState({ phase: "error", message: String(err) }),
    );

    return () => clearTimeout(timer);
  }, [fetchOverlay, fetchStatus]);

  return (
    <main className="flex flex-col items-center min-h-screen p-6 gap-6">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <header className="w-full max-w-5xl flex items-center justify-between">
        <button
          onClick={() => router.push("/")}
          className="text-sm text-gray-400 hover:text-white transition-colors flex items-center gap-1"
        >
          ← 홈
        </button>
        <h1 className="text-lg font-semibold text-gray-100">VOD Player</h1>
        <span className="font-mono text-xs text-gray-600 truncate max-w-[200px]">
          {jobId}
        </span>
      </header>

      {/* ── Content ────────────────────────────────────────────────────── */}
      {state.phase === "loading" && (
        <div className="text-gray-400 animate-pulse mt-20">로딩 중…</div>
      )}

      {state.phase === "polling" && (
        <div className="flex flex-col items-center gap-4 mt-20 text-center">
          <div className="w-12 h-12 border-4 border-indigo-500 border-t-transparent
                          rounded-full animate-spin" />
          <p className="text-gray-300 text-lg font-medium">파이프라인 처리 중</p>
          <StatusBadge status={state.status.status} />
          {state.status.error_message && (
            <p className="text-red-400 text-sm">{state.status.error_message}</p>
          )}
          <p className="text-xs text-gray-600">
            {POLL_INTERVAL_MS / 1000}초마다 자동 갱신됩니다
          </p>
        </div>
      )}

      {state.phase === "error" && (
        <div className="mt-20 text-center">
          <p className="text-red-400 text-xl mb-3">오류 발생</p>
          <p className="text-gray-400 text-sm">{state.message}</p>
          <button
            onClick={() => router.push("/")}
            className="mt-6 px-4 py-2 rounded-lg bg-gray-800 hover:bg-gray-700
                       text-sm transition-colors"
          >
            홈으로 돌아가기
          </button>
        </div>
      )}

      {state.phase === "ready" && (
        <VideoPlayer metadata={state.metadata} />
      )}
    </main>
  );
}

// ─── sub-components ──────────────────────────────────────────────────────────

// OverlayList has been moved into VideoPlayer for direct access to seekTo.

function StatusBadge({ status }: { status: string }) {
  const colours: Record<string, string> = {
    pending:       "bg-gray-700 text-gray-200",
    preprocessing: "bg-blue-900 text-blue-200",
    analysing:     "bg-yellow-900 text-yellow-200",
    persisting:    "bg-orange-900 text-orange-200",
    deciding:      "bg-purple-900 text-purple-200",
    complete:      "bg-green-900 text-green-200",
    failed:        "bg-red-900 text-red-200",
  };
  return (
    <span
      className={`px-3 py-1 rounded-full text-xs font-medium ${
        colours[status] ?? "bg-gray-800 text-gray-300"
      }`}
    >
      {status}
    </span>
  );
}

