import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "VOD Ad Overlay Player",
  description: "Dynamic contextual ad overlay for VOD content",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko">
      <body className="bg-gray-950 text-gray-100 min-h-screen antialiased">
        {children}
      </body>
    </html>
  );
}
