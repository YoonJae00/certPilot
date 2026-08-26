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

/** 상단 메뉴 링크. 손가락이 닿도록 높이 36px 를 확보한다. */
const NAV_LINK_CLASS =
  "inline-flex h-9 shrink-0 items-center whitespace-nowrap rounded-md px-2 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground";

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
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between gap-2 px-6 sm:gap-4">
        <div className="flex min-w-0 items-center gap-3 sm:gap-6">
          <Link
            href={home}
            className="inline-flex h-9 shrink-0 items-center text-base font-semibold tracking-tight"
          >
            CertPilot
          </Link>
          {/* 좁은 화면에서도 메뉴를 감추지 않는다(운영자가 검수 큐로 못 들어가는 문제). */}
          <nav className="flex min-w-0 items-center gap-1 overflow-x-auto [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden sm:gap-2">
            {isReviewer ? null : (
              <Link href="/projects" className={NAV_LINK_CLASS}>
                프로젝트
              </Link>
            )}
            {isReviewer || isOperator ? (
              <Link href="/review" className={NAV_LINK_CLASS}>
                검수 큐
              </Link>
            ) : null}
          </nav>
        </div>

        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          {user ? (
            <>
              <span
                className="hidden max-w-[220px] truncate text-sm text-muted-foreground sm:inline"
                title={user.email}
              >
                {user.email}
              </span>
              <Badge variant="secondary" className="whitespace-nowrap">
                {roleLabel(user.role)}
              </Badge>
            </>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            className="h-9 whitespace-nowrap"
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
