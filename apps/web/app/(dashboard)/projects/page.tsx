"use client";

import { useRouter } from "next/navigation";
import * as React from "react";

import { NewProjectDialog } from "@/app/(dashboard)/projects/new-project-dialog";
import { useUser } from "@/components/user-provider";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { isUnauthorized, projectsApi, toMessage } from "@/lib/api";
import { daysUntil, formatDate } from "@/lib/labels";
import type { Project } from "@/lib/types";

export default function ProjectsPage() {
  const router = useRouter();
  const { user } = useUser();
  const [projects, setProjects] = React.useState<Project[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const isOrgAdmin = user?.role === "org_admin";

  React.useEffect(() => {
    const controller = new AbortController();

    projectsApi
      .list(controller.signal)
      .then((data) => setProjects(data))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (isUnauthorized(err)) {
          router.replace("/login");
          return;
        }
        setError(toMessage(err));
        setProjects([]);
      });

    return () => controller.abort();
  }, [router]);

  function handleCreated(project: Project) {
    setProjects((prev) => (prev ? [project, ...prev] : [project]));
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">프로젝트</h1>
          <p className="text-sm text-muted-foreground">
            조직이 진행 중인 인증 프로젝트 목록입니다.
          </p>
        </div>
        {isOrgAdmin ? <NewProjectDialog onCreated={handleCreated} /> : null}
      </div>

      {error ? (
        <p
          role="alert"
          className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {error}
        </p>
      ) : null}

      {projects === null ? (
        <ProjectsSkeleton />
      ) : projects.length === 0 ? (
        <EmptyState canCreate={isOrgAdmin} />
      ) : (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                {/* 좁은 화면에서는 이름과 예정일만 남긴다(고정폭 3열이 이름을 세로로 무너뜨렸다). */}
                <TableRow>
                  <TableHead>이름</TableHead>
                  <TableHead className="hidden w-[120px] sm:table-cell">
                    인증 유형
                  </TableHead>
                  <TableHead className="hidden w-[120px] sm:table-cell">
                    간편인증
                  </TableHead>
                  <TableHead className="w-[130px] sm:w-[220px]">
                    사후심사 예정일
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {projects.map((project) => (
                  <TableRow
                    key={project.id}
                    className="cursor-pointer"
                    onClick={() => router.push(`/projects/${project.id}`)}
                  >
                    <TableCell className="break-keep font-medium">
                      {project.name}
                      {/* 숨긴 두 열의 내용은 좁은 화면에서 이름 아래에 한 줄로 남긴다. */}
                      <span className="mt-1 block text-xs font-normal text-muted-foreground sm:hidden">
                        {project.cert_type}
                        {project.is_simplified ? " · 간편인증 대상" : ""}
                      </span>
                    </TableCell>
                    <TableCell className="hidden sm:table-cell">
                      <Badge variant="outline" className="whitespace-nowrap">
                        {project.cert_type}
                      </Badge>
                    </TableCell>
                    <TableCell className="hidden sm:table-cell">
                      {project.is_simplified ? (
                        <Badge variant="secondary" className="whitespace-nowrap">
                          대상
                        </Badge>
                      ) : (
                        <span className="whitespace-nowrap text-muted-foreground">
                          해당 없음
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
                      <DueDateCell value={project.audit_due_date} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

/** 사후심사 예정일과 남은 기간(D-표기). */
function DueDateCell({ value }: { value: string | null }) {
  const remaining = daysUntil(value);
  if (!value) return <span className="text-muted-foreground">미지정</span>;

  return (
    <span className="flex flex-wrap items-center gap-x-2">
      <span className="whitespace-nowrap">{formatDate(value)}</span>
      {remaining !== null ? (
        <span
          className={
            remaining < 0
              ? "whitespace-nowrap text-xs text-destructive"
              : remaining <= 30
                ? "whitespace-nowrap text-xs font-medium text-warning"
                : "whitespace-nowrap text-xs text-muted-foreground"
          }
        >
          {remaining < 0 ? `${Math.abs(remaining)}일 경과` : `D-${remaining}`}
        </span>
      ) : null}
    </span>
  );
}

function ProjectsSkeleton() {
  return (
    <Card>
      <CardContent className="space-y-3 p-6">
        <Skeleton className="h-5 w-1/3" />
        <Skeleton className="h-5 w-2/3" />
        <Skeleton className="h-5 w-1/2" />
      </CardContent>
    </Card>
  );
}

function EmptyState({ canCreate }: { canCreate: boolean }) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center justify-center gap-2 py-16 text-center">
        <p className="text-sm font-medium">아직 등록된 프로젝트가 없습니다.</p>
        <p className="max-w-md text-sm text-muted-foreground">
          {canCreate
            ? "‘새 프로젝트’ 버튼으로 인증 준비를 시작할 대상 범위를 등록해 주세요."
            : "조직 관리자가 프로젝트를 등록하면 이곳에 표시됩니다."}
        </p>
      </CardContent>
    </Card>
  );
}
