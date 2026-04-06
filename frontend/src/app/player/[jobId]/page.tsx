"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import TVPlayer from "@/components/TVPlayer";
import type { JobStatus, OverlayMetadata } from "@/types/overlay";

const API              = "/api/backend";
const POLL_INTERVAL_MS = 5_000;

type PageState =
  | { phase: "loading" }
  | { phase: "polling"; status: JobStatus }
  | { phase: "ready";   metadata: OverlayMetadata }
  | { phase: "error";   message: string };

const STATUS_LABELS: Record<string, string> = {
  pending:       "대기 중",
  preprocessing: "전처리 중",
  analysing:     "영상 분석 중",
  persisting:    "데이터 저장 중",
  deciding:      "AI 광고 매칭 중",
  complete:      "완료",
  failed:        "실패",
};

export default function PlayerPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const router    = useRouter();
  const [state, setState] = useState<PageState>({ phase: "loading" });

  const goHome = useCallback(() => router.push("/?section=fastvod"), [router]);

  const fetchOverlay = useCallback(async () => {
    const res = await fetch(`${API}/overlay/${jobId}`);
    if (res.status === 200) {
      const data: OverlayMetadata = await res.json();
      setState({ phase: "ready", metadata: data });
      return true;
    }
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
      const done = await fetchOverlay();
      if (done) return;
      const status = await fetchStatus();
      if (!status) {
        setState({ phase: "error", message: "Job을 찾을 수 없습니다." });
        return;
      }
      if (status.status === "failed") {
        setState({ phase: "error", message: status.error_message ?? "파이프라인 오류" });
        return;
      }
      setState({ phase: "polling", status });
      timer = setTimeout(poll, POLL_INTERVAL_MS);
    }

    poll().catch((err) => setState({ phase: "error", message: String(err) }));
    return () => clearTimeout(timer);
  }, [fetchOverlay, fetchStatus]);

  /* ESC → 홈 복귀 */
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") goHome();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [goHome]);

  return (
    /* 풀스크린: 사이드바 마진 무효화, 100vw x 100vh */
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center"
      style={{ background: "#000" }}
    >
      {/* 로딩 */}
      {state.phase === "loading" && (
        <div className="flex flex-col items-center gap-4">
          <div
            className="w-10 h-10 rounded-full border-2 animate-spin"
            style={{ borderColor: "rgba(230,0,18,0.3)", borderTopColor: "#E60012" }}
          />
          <p className="text-sm" style={{ color: "#666" }}>불러오는 중…</p>
        </div>
      )}

      {/* 파이프라인 처리 중 */}
      {state.phase === "polling" && (
        <div className="flex flex-col items-center gap-6 text-center">
          <div
            className="w-16 h-16 rounded-full border-2 animate-spin"
            style={{ borderColor: "rgba(230,0,18,0.2)", borderTopColor: "#E60012" }}
          />
          <div>
            <p className="text-white text-xl font-bold mb-2">분석 중입니다</p>
            <p className="text-sm mb-4" style={{ color: "#666" }}>잠시만 기다려 주세요</p>
            <span
              className="inline-block px-5 py-2 rounded-full text-sm font-semibold"
              style={{ background: "rgba(230,0,18,0.12)", color: "#E60012", border: "1px solid rgba(230,0,18,0.25)" }}
            >
              {STATUS_LABELS[state.status.status] ?? state.status.status}
            </span>
          </div>
        </div>
      )}

      {/* 오류 */}
      {state.phase === "error" && (
        <div className="flex flex-col items-center gap-4 text-center">
          <div
            className="w-14 h-14 rounded-full flex items-center justify-center text-2xl"
            style={{ background: "rgba(230,0,18,0.12)" }}
          >⚠</div>
          <p className="text-white text-lg font-bold">오류가 발생했습니다</p>
          <p className="text-sm" style={{ color: "#666" }}>{state.message}</p>
          <button
            onClick={goHome}
            className="mt-3 px-7 py-2.5 rounded-xl text-sm font-semibold text-white hover:opacity-85 transition-opacity"
            style={{ background: "#E60012" }}
          >
            홈으로 돌아가기
          </button>
        </div>
      )}

      {/* 재생 준비 완료 → TV 스타일 풀스크린 플레이어 */}
      {state.phase === "ready" && (
        <TVPlayer metadata={state.metadata} onExit={goHome} />
      )}
    </div>
  );
}
