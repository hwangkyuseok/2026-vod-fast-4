/**
 * PlayerPage 컴포넌트 테스트
 *
 * TDD Red → Green → Refactor 사이클로 작성
 * 검증: 로딩 상태, 에러 상태, API 폴링, 키보드 이벤트
 */

import { describe, test, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

// ── Next.js mocks ──────────────────────────────────────────────────────────

const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useParams: () => ({ jobId: "test-job-123" }),
  useRouter: () => ({ push: mockPush }),
}));

// ── fetch mock 헬퍼 ────────────────────────────────────────────────────────

function mockFetchSequence(responses: Array<{ status: number; body: unknown }>) {
  let callIdx = 0;
  global.fetch = vi.fn().mockImplementation(() => {
    const resp = responses[callIdx] ?? responses[responses.length - 1];
    callIdx++;
    return Promise.resolve({
      ok: resp.status >= 200 && resp.status < 300,
      status: resp.status,
      json: () => Promise.resolve(resp.body),
    });
  });
}

// ── 동적 import (mocks 등록 후) ───────────────────────────────────────────

let PlayerPage: React.ComponentType;

beforeEach(async () => {
  vi.resetModules();
  mockPush.mockClear();
  // 동적 import로 mock이 적용된 상태에서 모듈 로드
  const mod = await import("@/app/player/[jobId]/page");
  PlayerPage = mod.default;
});

// HTMLMediaElement mock
beforeAll(() => {
  HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
  HTMLMediaElement.prototype.pause = vi.fn();
});

// ═══════════════════════════════════════════════════════════════════════════
// 1. 로딩 상태
// ═══════════════════════════════════════════════════════════════════════════

describe("PlayerPage: 로딩 상태", () => {
  test("초기 렌더링 시 로딩 표시가 나타난다", () => {
    // Arrange: fetch가 pending 상태로 유지
    global.fetch = vi.fn().mockImplementation(() => new Promise(() => {}));

    // Act
    render(<PlayerPage />);

    // Assert
    expect(screen.getByText("불러오는 중…")).toBeInTheDocument();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 2. 에러 상태
// ═══════════════════════════════════════════════════════════════════════════

describe("PlayerPage: 에러 상태", () => {
  test("overlay 404 + job 404이면 에러 메시지가 표시된다", async () => {
    // Arrange
    mockFetchSequence([
      { status: 404, body: {} },              // overlay → 실패
      { status: 404, body: null },            // job → 실패
    ]);

    // Act
    render(<PlayerPage />);

    // Assert
    await waitFor(() => {
      expect(screen.getByText("오류가 발생했습니다")).toBeInTheDocument();
    });
  });

  test("에러 상태에서 홈으로 돌아가기 버튼이 표시된다", async () => {
    // Arrange
    mockFetchSequence([
      { status: 404, body: {} },
      { status: 404, body: null },
    ]);

    // Act
    render(<PlayerPage />);

    // Assert
    await waitFor(() => {
      expect(screen.getByText("홈으로 돌아가기")).toBeInTheDocument();
    });
  });

  test("홈으로 돌아가기 클릭 시 메인으로 이동한다", async () => {
    // Arrange
    mockFetchSequence([
      { status: 404, body: {} },
      { status: 404, body: null },
    ]);
    render(<PlayerPage />);

    // Act
    await waitFor(() => {
      fireEvent.click(screen.getByText("홈으로 돌아가기"));
    });

    // Assert
    expect(mockPush).toHaveBeenCalledWith("/?section=fastvod");
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 3. 폴링 상태 (분석 진행 중)
// ═══════════════════════════════════════════════════════════════════════════

describe("PlayerPage: 폴링 상태", () => {
  test("분석 중이면 상태 메시지가 표시된다", async () => {
    // Arrange
    mockFetchSequence([
      { status: 404, body: {} },                                         // overlay → 아직 없음
      { status: 200, body: { job_id: "test", status: "analysing" } },   // job → 분석 중
    ]);

    // Act
    render(<PlayerPage />);

    // Assert
    await waitFor(() => {
      expect(screen.getByText("분석 중입니다")).toBeInTheDocument();
      expect(screen.getByText("영상 분석 중")).toBeInTheDocument();
    });
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 4. 키보드 이벤트
// ═══════════════════════════════════════════════════════════════════════════

describe("PlayerPage: 키보드 이벤트", () => {
  test("ESC 키를 누르면 홈으로 이동한다", async () => {
    // Arrange
    global.fetch = vi.fn().mockImplementation(() => new Promise(() => {}));
    render(<PlayerPage />);

    // Act
    fireEvent.keyDown(window, { key: "Escape" });

    // Assert
    expect(mockPush).toHaveBeenCalledWith("/?section=fastvod");
  });
});
