import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LG 헬로비전 · FAST VOD",
  description: "맥락 기반 AI 광고 오버레이 무료 VOD 서비스",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko">
      <body className="antialiased" style={{ background: "#0D0F18", color: "#FFFFFF", minHeight: "100vh" }}>
        {children}
      </body>
    </html>
  );
}
