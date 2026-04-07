"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

const NAV_ITEMS = [
  {
    label: "마이메뉴",
    section: null,
    href: null,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
      </svg>
    ),
  },
  {
    label: "검색",
    section: null,
    href: null,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
      </svg>
    ),
  },
  {
    label: "전체메뉴",
    section: null,
    href: null,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
      </svg>
    ),
  },
  {
    label: "홈",
    section: "home",
    href: "/?section=home",
    icon: (
      <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
        <path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z" />
      </svg>
    ),
  },
  {
    label: "영화/해외",
    section: "movies",
    href: "/?section=movies",
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M7 4v16M17 4v16M3 8h4m10 0h4M3 12h18M3 16h4m10 0h4M4 20h16a1 1 0 001-1V5a1 1 0 00-1-1H4a1 1 0 00-1 1v14a1 1 0 001 1z" />
      </svg>
    ),
  },
  {
    label: "FAST VOD",
    section: "fastvod",
    href: "/?section=fastvod",
    icon: (
      <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
        <path d="M13 2L3 14h9l-2 9 11-13h-9l1-8z" />
      </svg>
    ),
  },
  {
    label: "TV방송",
    section: null,
    href: null,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
      </svg>
    ),
  },
  {
    label: "애니/다큐",
    section: null,
    href: null,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  {
    label: "무제한관",
    section: null,
    href: null,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M5 3l14 9-14 9V3z" />
      </svg>
    ),
  },
  {
    label: "설정",
    section: null,
    href: null,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
  },
];

export default function Sidebar() {
  const router = useRouter();
  const [hoveredItem, setHoveredItem] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState<string>("movies");

  function handleClick(item: (typeof NAV_ITEMS)[0]) {
    if (!item.href) return;
    if (item.section) setActiveSection(item.section);
    router.push(item.href);
  }

  return (
    <aside
      className="fixed left-0 top-0 h-full flex flex-col items-center py-3 gap-0 z-50"
      style={{
        width: 68,
        background: "#161B2C",
        borderRight: "1px solid rgba(255,255,255,0.05)",
      }}
    >
      {NAV_ITEMS.map((item) => {
        const isActive = item.section !== null && item.section === activeSection;
        const isHovered = hoveredItem === item.label;

        return (
          <button
            key={item.label}
            onClick={() => handleClick(item)}
            onMouseEnter={() => setHoveredItem(item.label)}
            onMouseLeave={() => setHoveredItem(null)}
            title={item.label}
            className="w-full flex flex-col items-center gap-1 py-2 px-1 relative"
            style={{
              color: isActive || isHovered ? "#FFFFFF" : "#8892A4",
              background: isActive
                ? "rgba(255,255,255,0.08)"
                : isHovered
                ? "rgba(255,255,255,0.10)"
                : "transparent",
              borderLeft: isActive
                ? "3px solid #FFFFFF"
                : "3px solid transparent",
              transform: isHovered ? "scale(1.13)" : "scale(1)",
              transition: "transform 0.15s ease, color 0.15s ease, background 0.15s ease",
              cursor: item.href ? "pointer" : "default",
            }}
          >
            {item.icon}
            <span
              className="text-[9px] leading-tight text-center font-medium tracking-tight"
              style={{ color: "inherit" }}
            >
              {item.label}
            </span>
          </button>
        );
      })}
    </aside>
  );
}
