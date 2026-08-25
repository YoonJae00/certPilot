"use client";

/**
 * 세션 사용자 컨텍스트.
 *
 * 마운트 시 `GET /auth/me` 를 한 번 호출해 세션을 확인하고, 결과를 앱 전체에서 공유한다.
 * 미들웨어 없이 클라이언트 가드(AuthGuard)만으로 보호 라우트를 막는다.
 */

import * as React from "react";

import { authApi, isUnauthorized } from "@/lib/api";
import type { User } from "@/lib/types";

/** 세션 확인 진행 상태. */
type AuthStatus = "loading" | "authenticated" | "unauthenticated";

interface UserContextValue {
  user: User | null;
  status: AuthStatus;
  /** 로그인 성공 직후 응답 사용자를 그대로 반영한다. */
  setUser: (user: User | null) => void;
  /** 세션을 서버에서 다시 확인한다. */
  refresh: () => Promise<void>;
  /** 로그아웃 후 컨텍스트를 비운다. */
  logout: () => Promise<void>;
}

const UserContext = React.createContext<UserContextValue | null>(null);

export function UserProvider({ children }: { children: React.ReactNode }) {
  const [user, setUserState] = React.useState<User | null>(null);
  const [status, setStatus] = React.useState<AuthStatus>("loading");

  const load = React.useCallback(async (signal?: AbortSignal) => {
    try {
      const me = await authApi.me(signal);
      setUserState(me);
      setStatus("authenticated");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      // 401 뿐 아니라 서버 연결 실패도 미인증으로 다뤄 로그인 화면으로 보낸다.
      if (!isUnauthorized(error)) {
        console.warn("세션 확인 실패", error);
      }
      setUserState(null);
      setStatus("unauthenticated");
    }
  }, []);

  React.useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const setUser = React.useCallback((next: User | null) => {
    setUserState(next);
    setStatus(next ? "authenticated" : "unauthenticated");
  }, []);

  const refresh = React.useCallback(async () => {
    await load();
  }, [load]);

  const logout = React.useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      setUserState(null);
      setStatus("unauthenticated");
    }
  }, []);

  const value = React.useMemo<UserContextValue>(
    () => ({ user, status, setUser, refresh, logout }),
    [user, status, setUser, refresh, logout],
  );

  return <UserContext.Provider value={value}>{children}</UserContext.Provider>;
}

/** 세션 사용자 훅. UserProvider 안에서만 쓴다. */
export function useUser(): UserContextValue {
  const context = React.useContext(UserContext);
  if (!context) {
    throw new Error("useUser 는 UserProvider 안에서만 사용할 수 있습니다.");
  }
  return context;
}
