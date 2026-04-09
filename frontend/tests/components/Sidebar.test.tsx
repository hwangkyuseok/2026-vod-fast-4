/**
 * Sidebar 컴포넌트 테스트
 *
 * TDD Red → Green → Refactor 사이클로 작성
 * 검증: 메뉴 항목 렌더링, 네비게이션 동작
 */

import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom";
import Sidebar from "@/components/Sidebar";

// Next.js useRouter mock
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

// ═══════════════════════════════════════════════════════════════════════════
// 1. 메뉴 항목 렌더링
// ═══════════════════════════════════════════════════════════════════════════

describe("Sidebar: 메뉴 항목 렌더링", () => {
  test("필수 메뉴 항목들이 모두 표시된다", () => {
    // Arrange & Act
    render(<Sidebar />);

    // Assert
    expect(screen.getByTitle("홈")).toBeInTheDocument();
    expect(screen.getByTitle("FAST VOD")).toBeInTheDocument();
    expect(screen.getByTitle("검색")).toBeInTheDocument();
    expect(screen.getByTitle("설정")).toBeInTheDocument();
  });

  test("FAST VOD 메뉴가 존재한다", () => {
    // Arrange & Act
    render(<Sidebar />);

    // Assert
    expect(screen.getByTitle("FAST VOD")).toBeInTheDocument();
  });

  test("전체 메뉴 개수는 10개이다", () => {
    // Arrange & Act
    const { container } = render(<Sidebar />);

    // Assert
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBe(10);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 2. 네비게이션 동작
// ═══════════════════════════════════════════════════════════════════════════

describe("Sidebar: 네비게이션", () => {
  test("홈 클릭 시 /?section=home 으로 이동한다", () => {
    // Arrange
    mockPush.mockClear();
    render(<Sidebar />);

    // Act
    fireEvent.click(screen.getByTitle("홈"));

    // Assert
    expect(mockPush).toHaveBeenCalledWith("/?section=home");
  });

  test("FAST VOD 클릭 시 /?section=fastvod 으로 이동한다", () => {
    // Arrange
    mockPush.mockClear();
    render(<Sidebar />);

    // Act
    fireEvent.click(screen.getByTitle("FAST VOD"));

    // Assert
    expect(mockPush).toHaveBeenCalledWith("/?section=fastvod");
  });

  test("href가 없는 메뉴(마이메뉴)는 클릭해도 router.push가 호출되지 않는다", () => {
    // Arrange
    mockPush.mockClear();
    render(<Sidebar />);

    // Act
    fireEvent.click(screen.getByTitle("마이메뉴"));

    // Assert
    expect(mockPush).not.toHaveBeenCalled();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 3. 포커스 상태
// ═══════════════════════════════════════════════════════════════════════════

describe("Sidebar: 포커스 상태", () => {
  test("focusedIndex가 null이면 사이드바에 포커스 표시가 없다", () => {
    // Arrange & Act
    const { container } = render(<Sidebar focusedIndex={null} />);

    // Assert: 어떤 버튼도 scale(1.1) 강조를 받지 않음
    const aside = container.querySelector("aside") as HTMLElement;
    expect(aside.style.borderRight).toContain("rgba(255, 255, 255, 0.05)");
  });

  test("focusedIndex가 지정되면 사이드바 보더가 강조된다", () => {
    // Arrange & Act
    const { container } = render(<Sidebar focusedIndex={3} />);

    // Assert
    const aside = container.querySelector("aside") as HTMLElement;
    expect(aside.style.borderRight).toContain("rgba(255, 255, 255, 0.3)");
  });
});
