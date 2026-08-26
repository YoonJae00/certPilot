"use client";

/**
 * 문서 탭. 업로드 UI 뼈대 + 목록 테이블.
 *
 * 백엔드 문서 API(`POST/GET /projects/{id}/documents`)는 구현 진행 중이라,
 * 목록 조회가 404/501 로 실패해도 화면이 깨지지 않게 안내 문구로 대체한다.
 */

import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError, documentsApi, toMessage } from "@/lib/api";
import {
  DOCUMENT_STATUS_CLASSES,
  documentStatusLabel,
  formatBytes,
  formatDate,
} from "@/lib/labels";
import { cn } from "@/lib/utils";
import type { ProjectDocument } from "@/lib/types";

/** 업로드 허용 확장자. 서버 검증(pdf/docx/xlsx/md, 20MB)이 최종 기준이다. */
const ACCEPTED = ".pdf,.docx,.xlsx,.md";
/** 서버와 동일한 최대 업로드 크기(20MB). 미리 걸러 불필요한 요청을 줄인다. */
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

export function DocumentsTab({ projectId }: { projectId: string }) {
  const [documents, setDocuments] = React.useState<ProjectDocument[] | null>(null);
  const [notReady, setNotReady] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [file, setFile] = React.useState<File | null>(null);
  const [uploading, setUploading] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement>(null);

  const load = React.useCallback(
    async (signal?: AbortSignal) => {
      try {
        const data = await documentsApi.list(projectId, signal);
        setDocuments(data);
        setNotReady(false);
        setError(null);
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        // 아직 배포되지 않은 API 는 준비 중으로 표시한다.
        if (err instanceof ApiError && (err.status === 404 || err.status === 501)) {
          setNotReady(true);
          setDocuments([]);
          return;
        }
        setError(toMessage(err));
        setDocuments([]);
      }
    },
    [projectId],
  );

  React.useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  async function handleUpload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      toast.error("업로드할 파일을 선택해 주세요.");
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      toast.error("20MB 이하 파일만 업로드할 수 있습니다.");
      return;
    }

    setUploading(true);
    try {
      const created = await documentsApi.upload(projectId, file);
      setDocuments((prev) => (prev ? [created, ...prev] : [created]));
      setNotReady(false);
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      toast.success("문서를 업로드했습니다. 분석이 끝나면 상태가 바뀝니다.");
    } catch (err) {
      toast.error(toMessage(err));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="p-6">
          <form
            onSubmit={handleUpload}
            className="flex flex-col gap-3 sm:flex-row sm:items-center"
          >
            <Input
              ref={inputRef}
              type="file"
              accept={ACCEPTED}
              className="sm:max-w-sm"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              disabled={uploading}
              aria-label="업로드할 문서 파일"
            />
            <Button type="submit" disabled={uploading || !file}>
              {uploading ? "업로드 중…" : "업로드"}
            </Button>
            {file ? (
              <span className="text-xs text-muted-foreground">
                {file.name} · {formatBytes(file.size)}
              </span>
            ) : (
              <span className="text-xs text-muted-foreground">
                정책·지침·운영명세서 등 근거 문서(PDF, DOCX, XLSX, MD · 최대 20MB)를 올려 주세요.
              </span>
            )}
          </form>
          <p className="mt-3 text-xs text-muted-foreground">
            문서 상태가 ‘분석 완료’로 바뀌면 모의심사 탭에서 모의심사를 실행하세요.
          </p>
        </CardContent>
      </Card>

      {error ? (
        <p
          role="alert"
          className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {error}
        </p>
      ) : null}

      {notReady ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            문서 API 연동을 준비하고 있습니다. 연동이 끝나면 업로드한 문서가 여기에 표시됩니다.
          </CardContent>
        </Card>
      ) : documents === null ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            문서 목록을 불러오는 중입니다…
          </CardContent>
        </Card>
      ) : documents.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            업로드된 문서가 없습니다. 위에서 파일을 선택해 업로드해 주세요.
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                {/* 좁은 화면에서는 파일명·상태만 남기고, 페이지 수와 일시는 파일명 아래로 옮긴다. */}
                <TableRow>
                  <TableHead>파일명</TableHead>
                  <TableHead className="w-[96px] sm:w-[120px]">상태</TableHead>
                  {/* 페이지 수는 md 미만에서 파일명 아래 요약줄이 담당한다(중복 표시 방지). */}
                  <TableHead className="hidden w-[100px] md:table-cell">
                    페이지
                  </TableHead>
                  <TableHead className="hidden w-[160px] md:table-cell">
                    업로드 일시
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {documents.map((document) => (
                  <TableRow key={document.id}>
                    <TableCell className="break-all font-medium">
                      {document.filename}
                      {/* MIME 문자열은 좁은 화면에서 파일명보다 길어져 행을 잡아먹는다. */}
                      <span className="hidden text-xs font-normal text-muted-foreground sm:ml-2 sm:inline">
                        {document.mime}
                      </span>
                      <span className="mt-0.5 block text-xs font-normal text-muted-foreground md:hidden">
                        {formatDate(document.created_at)}
                        {document.page_count === null
                          ? ""
                          : ` · ${document.page_count}쪽`}
                      </span>
                    </TableCell>
                    <TableCell>
                      <Badge
                        className={cn(
                          "whitespace-nowrap",
                          DOCUMENT_STATUS_CLASSES[document.status],
                        )}
                        title={document.failure_reason ?? undefined}
                      >
                        {documentStatusLabel(document.status)}
                      </Badge>
                      {document.status === "failed" && document.failure_reason ? (
                        <p className="mt-1 text-xs text-muted-foreground">
                          {document.failure_reason}
                        </p>
                      ) : null}
                    </TableCell>
                    <TableCell className="hidden md:table-cell">
                      {document.page_count ?? (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell className="hidden whitespace-nowrap md:table-cell">
                      {formatDate(document.created_at)}
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
