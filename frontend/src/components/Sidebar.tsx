"use client";

import { usePathname, useRouter } from "next/navigation";

const NAV_ITEMS = [
  {
    label: "마이메뉴",
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
      </svg>
    ),
    href: null,
  },
  {
    label: "검색",
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
      </svg>
    ),
    href: null,
  },
  {
    label: "홈",
    icon: (
      <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
        <path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z" />
      </svg>
    ),
    href: "/",
    exact: true,
  },
  {
    label: "FAST VOD",
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
    href: "/",
  },
  {
    label: "TV방송",
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
      </svg>
    ),
    href: null,
  },
  {
    label: "영화/해외",
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M7 4v16M17 4v16M3 8h4m10 0h4M3 12h18M3 16h4m10 0h4M4 20h16a1 1 0 001-1V5a1 1 0 00-1-1H4a1 1 0 00-1 1v14a1 1 0 001 1z" />
      </svg>
    ),
    href: null,
  },
  {
    label: "전체메뉴",
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
      </svg>
    ),
    href: null,
  },
  {
    label: "설정",
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
    href: null,
  },
  {
    label: "알림",
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
      </svg>
    ),
    href: null,
  },
];

export default function Sidebar() {
  const pathname = usePathname();
  const router   = useRouter();

  return (
    <aside
      className="fixed left-0 top-0 h-full flex flex-col items-center py-4 gap-0.5 z-50"
      style={{
        width: 72,
        background: "#1A1A1A",
        borderRight: "1px solid rgba(255,255,255,0.06)",
      }}
    >
      {/* LG 헬로비전 로고 */}
      <div className="mb-5 flex flex-col items-center gap-1">
        <div
          className="w-9 h-9 rounded-lg flex items-center justify-center font-black text-xs text-white"
          style={{ background: "#E60012" }}
        >
          LG
        </div>
      </div>

      {/* 네비게이션 */}
      {NAV_ITEMS.map((item) => {
        const isActive =
          item.href !== null &&
          (item.exact ? pathname === item.href : pathname.startsWith(item.href)) &&
          item.label === "홈";

        return (
          <button
            key={item.label}
            onClick={() => item.href && router.push(item.href)}
            title={item.label}
            className="w-full flex flex-col items-center gap-1 py-2.5 px-1 rounded-lg transition-all"
            style={{
              color:      isActive ? "#E60012" : "#666666",
              background: isActive ? "rgba(230,0,18,0.12)" : "transparent",
            }}
            onMouseEnter={e => {
              if (!isActive) (e.currentTarget as HTMLElement).style.color = "#AAAAAA";
            }}
            onMouseLeave={e => {
              if (!isActive) (e.currentTarget as HTMLElement).style.color = "#666666";
            }}
          >
            {item.icon}
            <span className="text-[9px] leading-tight text-center font-medium">
              {item.label}
            </span>
          </button>
        );
      })}
    </aside>
  );
}
