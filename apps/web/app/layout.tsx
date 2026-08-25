import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CertPilot",
  description: "ISMS-P 준비·유지 코파일럿",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body className="antialiased">{children}</body>
    </html>
  );
}
