"use client";

/** 상단 네비게이션: 서비스명, 로그인 사용자 정보, 로그아웃. */

import Link from "next/link";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { useUser } from "@/components/user-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { toMessage } from "@/lib/api";
import { roleLabel } from "@/lib/labels";

export function SiteHeader() {
  const { user, logout } = useUser();
  const router = useRouter();
  const [pending, setPending] = React.useState(false);

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
          <Link href="/projects" className="text-base font-semibold tracking-tight">
            CertPilot
          </Link>
          <nav className="hidden sm:block">
            <Link
              href="/projects"
              className="text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              프로젝트
            </Link>
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
