"use client";

/**
 * 검수 화면 가드.
 *
 * 검수는 심사원 전용이다(운영자는 전 조직 열람 권한으로 읽기만 한다). 조직 사용자가
 * 주소를 직접 입력해 들어오면 프로젝트 목록으로 돌려보낸다. 서버도 같은 규칙으로
 * 403 을 주므로, 이 가드는 화면 흐름을 위한 것이지 보안 경계가 아니다.
 */

import { useRouter } from "next/navigation";
import * as React from "react";

import { useUser } from "@/components/user-provider";

export default function ReviewLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user, status } = useUser();
  const router = useRouter();
  const allowed = user?.role === "reviewer" || user?.role === "operator";

  React.useEffect(() => {
    if (status === "authenticated" && !allowed) {
      router.replace("/projects");
    }
  }, [status, allowed, router]);

  if (status === "authenticated" && !allowed) {
    return (
      <p className="text-sm text-muted-foreground" role="status">
        검수 화면은 심사원 전용입니다. 프로젝트 목록으로 이동합니다…
      </p>
    );
  }

  return <>{children}</>;
}
