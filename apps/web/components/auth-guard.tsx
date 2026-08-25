"use client";

/** 미인증 사용자를 /login 으로 보내는 클라이언트 가드. */

import { useRouter } from "next/navigation";
import * as React from "react";

import { useUser } from "@/components/user-provider";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { status } = useUser();
  const router = useRouter();

  React.useEffect(() => {
    if (status === "unauthenticated") {
      router.replace("/login");
    }
  }, [status, router]);

  if (status !== "authenticated") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-sm text-muted-foreground" role="status">
          {status === "loading" ? "세션을 확인하는 중입니다…" : "로그인 화면으로 이동합니다…"}
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
