"use client";

/** 상단 네비게이션: 서비스명, 역할별 메뉴, 로그인 사용자 정보, 로그아웃. */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { useUser } from "@/components/user-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { toMessage } from "@/lib/api";
import { roleLabel } from "@/lib/labels";

/** 역할별 첫 화면. 심사원은 조직 화면을 쓸 수 없어 검수 큐가 기본이다. */
function homeHrefFor(role: string | undefined): string {
  return role === "reviewer" ? "/review" : "/projects";
}

export function SiteHeader() {
  const { user, logout } = useUser();
  const router = useRouter();
  const pathname = usePathname();
  const [pending, setPending] = React.useState(false);

  const isReviewer = user?.role === "reviewer";
  const isOperator = user?.role === "operator";
  const home = homeHrefFor(user?.role);

  // 심사원이 조직 화면으로 들어오면(로그인 직후 기본 이동 포함) 검수 큐로 되돌린다.
  // 서버는 조직 API 에 403 을 주므로 화면만 봐도 아무것도 할 수 없다.
  React.useEffect(() => {
    if (isReviewer && pathname?.startsWith("/projects")) {
      router.replace("/review");
    }
  }, [isReviewer, pathname, router]);

  async function handleLogout() {
    setPending(true);
    try {
      await logout();
      router.replace("/login");
    } catch (error) {
      toast.error(toMessage(error));
    } finally {
      setPending(false);
    }
  }

  return (
    <header className="sticky top-0 z-40 border-b bg-background">
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between gap-4 px-6">
        <div className="flex items-center gap-6">
          <Link href={home} className="text-base font-semibold tracking-tight">
            CertPilot
          </Link>
          <nav className="hidden items-center gap-4 sm:flex">
            {isReviewer ? null : (
              <Link
                href="/projects"
                className="text-sm text-muted-foreground transition-colors hover:text-foreground"
              >
                프로젝트
              </Link>
            )}
            {isReviewer || isOperator ? (
              <Link
                href="/review"
                className="text-sm text-muted-foreground transition-colors hover:text-foreground"
              >
                검수 큐
              </Link>
            ) : null}
          </nav>
        </div>

        <div className="flex items-center gap-3">
          {user ? (
            <>
              <span
                className="hidden max-w-[220px] truncate text-sm text-muted-foreground sm:inline"
                title={user.email}
              >
                {user.email}
              </span>
              <Badge variant="secondary">{roleLabel(user.role)}</Badge>
            </>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            onClick={handleLogout}
            disabled={pending}
          >
            {pending ? "로그아웃 중…" : "로그아웃"}
          </Button>
        </div>
      </div>
    </header>
  );
}
