import type { Metadata } from "next";

import { Toaster } from "@/components/ui/sonner";
import { UserProvider } from "@/components/user-provider";

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
      <body className="min-h-screen bg-background antialiased">
        <UserProvider>{children}</UserProvider>
        <Toaster position="top-center" richColors />
      </body>
    </html>
  );
}
