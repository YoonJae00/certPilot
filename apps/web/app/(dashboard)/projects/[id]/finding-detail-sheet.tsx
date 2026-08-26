"use client";

/**
 * 판정 상세 드로어.
 *
 * 리포트 테이블에서 행을 누르면 열리고, 그때 상세 API 를 한 번 호출한다.
 * 근거 청크는 `<mark>` 배경으로 강조해 "이 문장이 판정의 근거"라는 걸 드러낸다.
 * 본문의 `chunk:`/`evidence:` 참조는 각주 칩으로 바꿔 아래 근거 카드와 이어 붙인다
 * (`rationale-citations.tsx`).
 */

import * as React from "react";

import {
  CitationNumberBadge,
  CitationProvider,
  CitedText,
  useCitationAnchor,
} from "@/app/(dashboard)/projects/[id]/rationale-citations";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { assessmentsApi, toMessage } from "@/lib/api";
import {
  DECIDED_BY_LABELS,
  FINDING_STATUS_CLASSES,
  FINDING_STATUS_LABELS,
  formatPercent,
} from "@/lib/labels";
import type {
  FindingChunk,
  FindingDetail,
  FindingEvidence,
  FindingRow,
} from "@/lib/types";
import { cn } from "@/lib/utils";

export function FindingDetailSheet({
  projectId,
  assessmentId,
  finding,
  onOpenChange,
  highlight,
}: {
  projectId: string;
  assessmentId: string;
  /** 열려 있는 행. null 이면 드로어를 닫는다. */
  finding: FindingRow | null;
  onOpenChange: (open: boolean) => void;
  /** 리포트 검색어. 근거 본문에서 같은 단어를 더 진하게 표시한다. */
  highlight?: string;
}) {
  const [detail, setDetail] = React.useState<FindingDetail | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const findingId = finding?.id ?? null;

  React.useEffect(() => {
    if (!findingId) {
      setDetail(null);
      setError(null);
      return;
    }
    const controller = new AbortController();
    setDetail(null);
    setError(null);

    assessmentsApi
      .finding(projectId, assessmentId, findingId, controller.signal)
      .then((data) => setDetail(data))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(toMessage(err));
      });

    return () => controller.abort();
  }, [assessmentId, findingId, projectId]);

  // 상세를 받기 전에는 테이블 행에 있던 값으로 먼저 채운다.
  const row: FindingRow | null = detail ?? finding;
  const hasEvidence =
    (detail?.chunks.length ?? 0) > 0 || (detail?.evidence.length ?? 0) > 0;

  return (
    <Sheet open={finding !== null} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full overflow-y-auto sm:max-w-xl lg:max-w-2xl"
      >
        {row ? (
          <>
            <SheetHeader className="pr-8">
              <SheetTitle className="text-left">
                <span className="tabular-nums text-muted-foreground">
                  {row.criterion_code}
                </span>{" "}
                {row.title}
              </SheetTitle>
              <SheetDescription className="text-left">
                {row.section}
              </SheetDescription>
            </SheetHeader>

            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Badge className={cn(FINDING_STATUS_CLASSES[row.status])}>
                {FINDING_STATUS_LABELS[row.status]}
              </Badge>
              <Badge variant="outline">
                신뢰도 {formatPercent(row.confidence)}
              </Badge>
              <Badge variant="secondary">
                판정 주체 {DECIDED_BY_LABELS[row.decided_by] ?? row.decided_by}
              </Badge>
            </div>

            {error ? (
              <p
                role="alert"
                className="mt-4 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                {error}
              </p>
            ) : null}

            {detail === null && error === null ? (
              <div className="mt-6 space-y-3">
                <Skeleton className="h-4 w-1/3" />
                <Skeleton className="h-20 w-full" />
                <Skeleton className="h-20 w-full" />
              </div>
            ) : null}

            {detail ? (
              <CitationProvider
                chunks={detail.chunks}
                evidence={detail.evidence}
                texts={[
                  detail.rationale,
                  detail.predicted_defect,
                  detail.recommendation,
                ]}
              >
                <div className="mt-6 space-y-6 pb-6">
                  <Section title="판정 근거">
                    {detail.rationale.trim() ? (
                      <p className="whitespace-pre-wrap text-sm leading-relaxed">
                        <CitedText text={detail.rationale} />
                      </p>
                    ) : (
                      <EmptyText>기록된 판정 근거가 없습니다.</EmptyText>
                    )}
                  </Section>

                  <Section title={`근거 문서 (${detail.chunks.length}건)`}>
                    {detail.chunks.length === 0 ? (
                      <EmptyText>인용된 문서 근거가 없습니다.</EmptyText>
                    ) : (
                      <div className="space-y-3">
                        {detail.chunks.map((chunk) => (
                          <ChunkCard
                            key={chunk.chunk_id}
                            chunk={chunk}
                            highlight={highlight}
                          />
                        ))}
                      </div>
                    )}
                  </Section>

                  <Section title={`클라우드 증적 (${detail.evidence.length}건)`}>
                    {detail.evidence.length === 0 ? (
                      <EmptyText>연결된 커넥터 증적이 없습니다.</EmptyText>
                    ) : (
                      <div className="space-y-3">
                        {detail.evidence.map((item) => (
                          <EvidenceCard key={item.evidence_id} item={item} />
                        ))}
                      </div>
                    )}
                  </Section>

                  {!hasEvidence ? (
                    <p className="rounded-md bg-muted px-3 py-2 text-sm text-muted-foreground">
                      {detail.status === "unknown"
                        ? "근거 없음 — 판단불가로 처리됐습니다. 관련 문서를 업로드하거나 커넥터를 연결한 뒤 다시 실행해 주세요."
                        : "근거 없음 — 인용된 문서·증적이 없습니다. 관련 문서를 업로드하거나 커넥터를 연결한 뒤 다시 실행해 주세요."}
                    </p>
                  ) : null}

                  <Section title="예상 결함">
                    {detail.predicted_defect?.trim() ? (
                      <p className="whitespace-pre-wrap rounded-md bg-destructive/10 px-3 py-2 text-sm leading-relaxed text-destructive">
                        <CitedText text={detail.predicted_defect} />
                      </p>
                    ) : (
                      <EmptyText>예상되는 결함이 기록되지 않았습니다.</EmptyText>
                    )}
                  </Section>

                  <Section title="개선 권고">
                    {detail.recommendation?.trim() ? (
                      <p className="whitespace-pre-wrap text-sm leading-relaxed">
                        <CitedText text={detail.recommendation} />
                      </p>
                    ) : (
                      <EmptyText>개선 권고가 기록되지 않았습니다.</EmptyText>
                    )}
                  </Section>
                </div>
              </CitationProvider>
            ) : null}
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </h3>
      {children}
    </section>
  );
}

function EmptyText({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>;
}

/** 근거 문서 1건. 본문 각주 칩이 id 로 이 카드를 찾아 스크롤·플래시한다. */
function ChunkCard({
  chunk,
  highlight,
}: {
  chunk: FindingChunk;
  highlight?: string;
}) {
  const anchor = useCitationAnchor(chunk.chunk_id);

  return (
    <Card id={anchor.id} className={anchor.className}>
      <CardContent className="space-y-2 p-4">
        <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <CitationNumberBadge id={chunk.chunk_id} />
          <span className="font-medium text-foreground">{chunk.filename}</span>
          <span>
            {chunk.page === null ? "페이지 정보 없음" : `${chunk.page}쪽`}
          </span>
        </p>
        <mark className="block whitespace-pre-wrap rounded-md bg-warning/15 p-3 text-sm leading-relaxed text-foreground">
          <HighlightedText text={chunk.text} query={highlight} />
        </mark>
      </CardContent>
    </Card>
  );
}

/** 클라우드 증적 1건. payload_json 은 점검별로 구조가 달라 키·값 목록으로 편다. */
function EvidenceCard({ item }: { item: FindingEvidence }) {
  const anchor = useCitationAnchor(item.evidence_id);
  const payload: unknown = item.payload_json;
  const entries =
    payload && typeof payload === "object" && !Array.isArray(payload)
      ? Object.entries(payload as Record<string, unknown>)
      : [];

  return (
    <Card id={anchor.id} className={anchor.className}>
      <CardContent className="space-y-2 p-4">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <CitationNumberBadge id={item.evidence_id} />
          <Badge variant="outline" className="uppercase">
            {item.source}
          </Badge>
          <span className="font-medium">{item.check_id}</span>
          <Badge variant="secondary">{item.status}</Badge>
        </div>
        {entries.length > 0 ? (
          <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[minmax(0,10rem)_1fr]">
            {entries.map(([key, value]) => (
              <React.Fragment key={key}>
                <dt className="truncate text-muted-foreground">{key}</dt>
                <dd className="break-words font-mono text-[11px]">
                  {formatPayloadValue(value)}
                </dd>
              </React.Fragment>
            ))}
          </dl>
        ) : (
          <EmptyText>수집된 상세 값이 없습니다.</EmptyText>
        )}
      </CardContent>
    </Card>
  );
}

/** payload 값 1개를 한 줄 문자열로 만든다. */
function formatPayloadValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** 검색어와 일치하는 부분을 한 단계 더 진한 `<mark>` 로 감싼다. */
function HighlightedText({
  text,
  query,
}: {
  text: string;
  query?: string;
}) {
  const term = query?.trim() ?? "";
  if (term.length < 2 || !text.toLowerCase().includes(term.toLowerCase())) {
    return <>{text}</>;
  }

  const parts: React.ReactNode[] = [];
  const lower = text.toLowerCase();
  const needle = term.toLowerCase();
  let cursor = 0;

  for (;;) {
    const found = lower.indexOf(needle, cursor);
    if (found === -1) break;
    if (found > cursor) parts.push(text.slice(cursor, found));
    parts.push(
      <mark
        key={`${found}-${needle}`}
        className="rounded bg-warning/50 px-0.5 text-foreground"
      >
        {text.slice(found, found + needle.length)}
      </mark>,
    );
    cursor = found + needle.length;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));

  return <>{parts}</>;
}
