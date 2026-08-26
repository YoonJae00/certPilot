"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import * as React from "react";

import { AssessmentTab } from "@/app/(dashboard)/projects/[id]/assessment-tab";
import { DashboardTab } from "@/app/(dashboard)/projects/[id]/dashboard-tab";
import { DocumentsTab } from "@/app/(dashboard)/projects/[id]/documents-tab";
import { GraphTab } from "@/app/(dashboard)/projects/[id]/graph-tab";
import { ReportTab } from "@/app/(dashboard)/projects/[id]/report-tab";
import { useAssessments } from "@/app/(dashboard)/projects/[id]/use-assessments";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { isUnauthorized, projectsApi, toMessage } from "@/lib/api";
import { daysUntil, formatDate } from "@/lib/labels";
import type { Project } from "@/lib/types";

export default function ProjectDetailPage() {
  const params = useParams<{ id: string }>();
  const projectId = params?.id ?? "";
  const router = useRouter();
  const [project, setProject] = React.useState<Project | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  // 모의심사 탭과 리포트 탭이 같은 실행을 가리키도록 상태를 여기서 들고 있는다.
  // 기본 탭은 유지 대시보드다(PRD §7 F8 — 들어오자마자 준비도·알림을 본다).
  const [tab, setTab] = React.useState("dashboard");
  const assessments = useAssessments(projectId);

  React.useEffect(() => {
    if (!projectId) return;
    const controller = new AbortController();

    projectsApi
      .get(projectId, controller.signal)
      .then((data) => setProject(data))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (isUnauthorized(err)) {
          router.replace("/login");
          return;
        }
        setError(toMessage(err));
      });

    return () => controller.abort();
  }, [projectId, router]);

  if (error) {
    return (
      <div className="space-y-4">
        <p
          role="alert"
          className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {error}
        </p>
        <Link
          href="/projects"
          className="text-sm text-muted-foreground underline underline-offset-4"
        >
          프로젝트 목록으로 돌아가기
        </Link>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  const remaining = daysUntil(project.audit_due_date);

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <Link
          href="/projects"
          className="text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          ← 프로젝트 목록
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">{project.name}</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">프로젝트 개요</CardTitle>
          <CardDescription>인증 범위와 일정 정보입니다.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="인증 유형">
            <Badge variant="outline">{project.cert_type}</Badge>
          </Field>
          <Field label="간편인증">
            {project.is_simplified ? (
              <Badge variant="secondary">대상</Badge>
            ) : (
              <span className="text-sm text-muted-foreground">해당 없음</span>
            )}
          </Field>
          <Field label="사후심사 예정일">
            <span className="text-sm">
              {formatDate(project.audit_due_date)}
              {remaining !== null ? (
                <span
                  className={
                    remaining < 0
                      ? "ml-2 text-xs text-destructive"
                      : remaining <= 30
                        ? "ml-2 text-xs font-medium text-warning"
                        : "ml-2 text-xs text-muted-foreground"
                  }
                >
                  {remaining < 0 ? `${Math.abs(remaining)}일 경과` : `D-${remaining}`}
                </span>
              ) : null}
            </span>
          </Field>
          <Field label="등록일">
            <span className="text-sm">{formatDate(project.created_at)}</span>
          </Field>
          <div className="sm:col-span-2 lg:col-span-4">
            <Field label="인증 범위">
              <p className="whitespace-pre-wrap text-sm">
                {project.scope_text?.trim() ? (
                  project.scope_text
                ) : (
                  <span className="text-muted-foreground">
                    아직 인증 범위가 입력되지 않았습니다.
                  </span>
                )}
              </p>
            </Field>
          </div>
        </CardContent>
      </Card>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="dashboard">대시보드</TabsTrigger>
          <TabsTrigger value="documents">문서</TabsTrigger>
          <TabsTrigger value="assessment">모의심사</TabsTrigger>
          <TabsTrigger value="report">리포트</TabsTrigger>
          <TabsTrigger value="graph">지식 그래프</TabsTrigger>
        </TabsList>

        <TabsContent value="dashboard" className="mt-4">
          <DashboardTab
            projectId={project.id}
            onGoToAssessment={() => setTab("assessment")}
          />
        </TabsContent>

        <TabsContent value="documents" className="mt-4">
          <DocumentsTab projectId={project.id} />
        </TabsContent>

        <TabsContent value="assessment" className="mt-4">
          <AssessmentTab
            assessments={assessments}
            onGoToReport={() => setTab("report")}
          />
        </TabsContent>

        <TabsContent value="report" className="mt-4">
          <ReportTab
            projectId={project.id}
            projectName={project.name}
            assessments={assessments}
            onGoToAssessment={() => setTab("assessment")}
          />
        </TabsContent>

        <TabsContent value="graph" className="mt-4">
          <GraphTab
            projectId={project.id}
            assessments={assessments}
            onGoToAssessment={() => setTab("assessment")}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <div>{children}</div>
    </div>
  );
}
