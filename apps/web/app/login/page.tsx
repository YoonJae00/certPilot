"use client";

import { useRouter } from "next/navigation";
import * as React from "react";

import { useUser } from "@/components/user-provider";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, authApi, toMessage } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const { status, setUser } = useUser();
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [pending, setPending] = React.useState(false);

  // 이미 로그인된 상태로 들어오면 곧장 프로젝트 목록으로 보낸다.
  React.useEffect(() => {
    if (status === "authenticated") {
      router.replace("/projects");
    }
  }, [status, router]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (!email.trim() || !password) {
      setError("이메일과 비밀번호를 모두 입력해 주세요.");
      return;
    }

    setPending(true);
    try {
      const user = await authApi.login({ email: email.trim(), password });
      setUser(user);
      router.replace("/projects");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("이메일 또는 비밀번호가 올바르지 않습니다.");
      } else {
        setError(toMessage(err));
      }
      setPending(false);
    }
  }

  // 계정 없이 시드된 데모핀테크 데이터를 둘러본다. 서버가 기능을 껐으면 404 다.
  async function handleDemoLogin() {
    setError(null);
    setPending(true);
    try {
      const user = await authApi.demoLogin();
      setUser(user);
      router.replace("/projects");
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setError("데모 체험이 준비되어 있지 않습니다.");
      } else {
        setError(toMessage(err));
      }
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted/40 px-6 py-12">
      <div className="w-full max-w-sm space-y-6">
        <div className="space-y-1 text-center">
          <h1 className="text-2xl font-semibold tracking-tight">CertPilot</h1>
          <p className="text-sm text-muted-foreground">
            ISMS-P 준비·유지 코파일럿
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>로그인</CardTitle>
            <CardDescription>
              발급받은 계정으로 로그인해 주세요. 계정은 운영자가 생성합니다.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4" noValidate>
              <div className="space-y-2">
                <Label htmlFor="email">이메일</Label>
                <Input
                  id="email"
                  name="email"
                  type="email"
                  autoComplete="username"
                  placeholder="name@example.com"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  disabled={pending}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="password">비밀번호</Label>
                <Input
                  id="password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  placeholder="비밀번호를 입력하세요"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={pending}
                />
              </div>

              {error ? (
                <p
                  role="alert"
                  className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
                >
                  {error}
                </p>
              ) : null}

              <Button type="submit" className="w-full" disabled={pending}>
                {pending ? "로그인 중…" : "로그인"}
              </Button>
            </form>

            <div className="mt-6 space-y-3">
              <div className="flex items-center gap-3">
                <span className="h-px flex-1 bg-border" />
                <span className="text-xs text-muted-foreground">또는</span>
                <span className="h-px flex-1 bg-border" />
              </div>

              <Button
                type="button"
                variant="outline"
                className="w-full"
                disabled={pending}
                onClick={handleDemoLogin}
              >
                데모 계정으로 둘러보기
              </Button>

              <p className="text-center text-xs text-muted-foreground">
                계정 없이 예시 회사 &lsquo;데모핀테크&rsquo;의 데이터를 둘러봅니다.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
