import { AuthGuard } from "@/components/auth-guard";
import { SiteHeader } from "@/components/site-header";

/** 인증이 필요한 화면들의 공통 레이아웃. */
export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AuthGuard>
      <div className="flex min-h-screen flex-col">
        <SiteHeader />
        <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
          {children}
        </main>
      </div>
    </AuthGuard>
  );
}
