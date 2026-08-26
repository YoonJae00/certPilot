"use client";

/**
 * 운영명세서 검수 표(101행).
 *
 * 운영 현황·담당 부서·비고 칸만 편집할 수 있다. 항목 코드·항목명·관련 문서는 지식베이스와
 * 판정에서 온 값이라 심사원이 바꾸지 않는다(내용을 지어내지 않는다는 원칙).
 *
 * 칸을 클릭하면 그 자리에서 입력으로 바뀌고, 실제 저장은 하단 바의 "저장" 버튼이 한 번에
 * 보낸다(101행을 칸마다 저장하면 요청이 폭증하고 DOCX 재생성도 그만큼 돈다).
 *
 * 표는 6열 728px 이라 좁은 화면에서는 편집할 칸이 화면 밖으로 밀려난다. 그래서 md 미만에서는
 * 행 하나를 세로 카드로 펼친다. 편집 상태와 저장 경로는 표와 완전히 같고 표현만 갈라진다.
 */

import * as React from "react";

import { ReviewTextarea } from "@/app/(dashboard)/review/textarea";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { NEEDS_REVIEW } from "@/lib/api-review";
import { cn } from "@/lib/utils";
import type { SowEditableField, SowRow } from "@/lib/types-review";

interface SowEditorProps {
  rows: SowRow[];
  /** 저장되지 않은 변경이 있는 행 번호. */
  dirtyRows: ReadonlySet<number>;
  disabled: boolean;
  onChange: (rowIndex: number, field: SowEditableField, value: string) => void;
}

/** 편집 중인 칸 좌표. */
interface CellPosition {
  row: number;
  field: SowEditableField;
}

/** 카드로 펼칠 기준 폭(md 미만). */
const COMPACT_QUERY = "(max-width: 767.98px)";

/**
 * 좁은 화면인지.
 *
 * CSS 로 표와 카드를 동시에 그려 두면 편집 칸의 aria-label 이 두 벌 생겨 스크린리더와
 * e2e 가 어느 쪽을 잡을지 알 수 없다. 그래서 한 번에 한 쪽만 렌더링한다.
 * 서버 렌더 스냅샷은 표(false)로 둔다.
 */
function useIsCompact(): boolean {
  const subscribe = React.useCallback((onStoreChange: () => void) => {
    const query = window.matchMedia(COMPACT_QUERY);
    query.addEventListener("change", onStoreChange);
    return () => query.removeEventListener("change", onStoreChange);
  }, []);

  return React.useSyncExternalStore(
    subscribe,
    () => window.matchMedia(COMPACT_QUERY).matches,
    () => false,
  );
}

export function SowEditor({
  rows,
  dirtyRows,
  disabled,
  onChange,
}: SowEditorProps) {
  const [editing, setEditing] = React.useState<CellPosition | null>(null);
  const compact = useIsCompact();

  function isEditing(row: number, field: SowEditableField): boolean {
    return editing?.row === row && editing.field === field;
  }

  if (compact) {
    return (
      <ul className="space-y-3 p-3">
        {rows.map((row, index) => (
          <li
            key={`${row.criterion_code}-${index}`}
            className={cn(
              "space-y-3 rounded-md border p-3",
              dirtyRows.has(index) && "bg-warning/10",
            )}
          >
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
              <span className="font-mono text-xs text-muted-foreground">
                {row.criterion_code}
              </span>
              <span className="break-keep text-sm font-medium">
                {row.criterion_title}
              </span>
            </div>

            <CardField label="운영 현황">
              <EditableCell
                label={`${row.criterion_code} 운영 현황`}
                value={row.operation_status}
                multiline
                editing={isEditing(index, "operation_status")}
                disabled={disabled}
                onStartEdit={() =>
                  setEditing({ row: index, field: "operation_status" })
                }
                onStopEdit={() => setEditing(null)}
                onChange={(value) => onChange(index, "operation_status", value)}
              />
            </CardField>

            <CardField label="관련 문서·증적">
              {row.related_refs.length === 0 ? (
                <p className="px-1.5 text-xs text-muted-foreground">—</p>
              ) : (
                <ul className="space-y-1 px-1.5 text-xs text-muted-foreground">
                  {row.related_refs.map((ref, refIndex) => (
                    <li key={`${ref}-${refIndex}`} className="break-all">
                      {ref}
                    </li>
                  ))}
                </ul>
              )}
            </CardField>

            <CardField label="담당 부서">
              <EditableCell
                label={`${row.criterion_code} 담당 부서`}
                value={row.owner_dept}
                editing={isEditing(index, "owner_dept")}
                disabled={disabled}
                onStartEdit={() =>
                  setEditing({ row: index, field: "owner_dept" })
                }
                onStopEdit={() => setEditing(null)}
                onChange={(value) => onChange(index, "owner_dept", value)}
              />
            </CardField>

            <CardField label="비고">
              <EditableCell
                label={`${row.criterion_code} 비고`}
                value={row.note}
                multiline
                editing={isEditing(index, "note")}
                disabled={disabled}
                onStartEdit={() => setEditing({ row: index, field: "note" })}
                onStopEdit={() => setEditing(null)}
                onChange={(value) => onChange(index, "note", value)}
              />
            </CardField>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div>
      <Table>
        <TableHeader>
          {/* w- 는 힌트일 뿐이라 태블릿 폭에서 뒤쪽 열이 한 글자씩 세로로 눌렸다.
              min-w- 로 바꿔 눌리는 대신 표가 가로로 스크롤되게 한다(래퍼가 힌트를 그린다). */}
          <TableRow>
            <TableHead className="w-[84px]">코드</TableHead>
            <TableHead className="min-w-[140px]">항목명</TableHead>
            <TableHead className="min-w-[320px]">운영 현황</TableHead>
            <TableHead className="min-w-[160px]">관련 문서·증적</TableHead>
            <TableHead className="min-w-[120px]">담당 부서</TableHead>
            <TableHead className="min-w-[180px]">비고</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, index) => (
            <TableRow
              key={`${row.criterion_code}-${index}`}
              className={cn(dirtyRows.has(index) && "bg-warning/10")}
            >
              <TableCell className="align-top font-mono text-xs">
                {row.criterion_code}
              </TableCell>
              <TableCell className="break-keep align-top text-xs">
                {row.criterion_title}
              </TableCell>

              <TableCell className="align-top">
                <EditableCell
                  label={`${row.criterion_code} 운영 현황`}
                  value={row.operation_status}
                  multiline
                  editing={isEditing(index, "operation_status")}
                  disabled={disabled}
                  onStartEdit={() =>
                    setEditing({ row: index, field: "operation_status" })
                  }
                  onStopEdit={() => setEditing(null)}
                  onChange={(value) =>
                    onChange(index, "operation_status", value)
                  }
                />
              </TableCell>

              <TableCell className="align-top text-xs text-muted-foreground">
                {row.related_refs.length === 0 ? (
                  "—"
                ) : (
                  <ul className="space-y-1">
                    {row.related_refs.map((ref, refIndex) => (
                      <li key={`${ref}-${refIndex}`}>{ref}</li>
                    ))}
                  </ul>
                )}
              </TableCell>

              <TableCell className="align-top">
                <EditableCell
                  label={`${row.criterion_code} 담당 부서`}
                  value={row.owner_dept}
                  editing={isEditing(index, "owner_dept")}
                  disabled={disabled}
                  onStartEdit={() =>
                    setEditing({ row: index, field: "owner_dept" })
                  }
                  onStopEdit={() => setEditing(null)}
                  onChange={(value) => onChange(index, "owner_dept", value)}
                />
              </TableCell>

              <TableCell className="align-top">
                <EditableCell
                  label={`${row.criterion_code} 비고`}
                  value={row.note}
                  multiline
                  editing={isEditing(index, "note")}
                  disabled={disabled}
                  onStartEdit={() => setEditing({ row: index, field: "note" })}
                  onStopEdit={() => setEditing(null)}
                  onChange={(value) => onChange(index, "note", value)}
                />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/** 카드 안의 한 필드. 표의 열 머리글 역할을 라벨이 대신한다. */
function CardField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <p className="px-1.5 text-[11px] font-medium text-muted-foreground">
        {label}
      </p>
      {children}
    </div>
  );
}

interface EditableCellProps {
  /** 스크린리더·e2e 가 칸을 특정할 수 있게 붙이는 이름. */
  label: string;
  value: string;
  multiline?: boolean;
  editing: boolean;
  disabled: boolean;
  onStartEdit: () => void;
  onStopEdit: () => void;
  onChange: (value: string) => void;
}

function EditableCell({
  label,
  value,
  multiline = false,
  editing,
  disabled,
  onStartEdit,
  onStopEdit,
  onChange,
}: EditableCellProps) {
  const needsReview = value.includes(NEEDS_REVIEW);

  if (editing && !disabled) {
    const shared = {
      autoFocus: true,
      "aria-label": label,
      value,
      onChange: (
        event: React.ChangeEvent<HTMLTextAreaElement | HTMLInputElement>,
      ) => onChange(event.target.value),
      onBlur: onStopEdit,
      onKeyDown: (
        event: React.KeyboardEvent<HTMLTextAreaElement | HTMLInputElement>,
      ) => {
        // Esc 로 편집을 닫는다. 입력값은 이미 반영돼 있고 저장은 하단 바에서 한다.
        if (event.key === "Escape") onStopEdit();
      },
    };
    return multiline ? (
      <ReviewTextarea {...shared} rows={4} className="text-xs" />
    ) : (
      <Input {...shared} className="h-8 text-xs" />
    );
  }

  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onStartEdit}
      className={cn(
        // 좁은 화면에서는 손가락이 닿게 최소 높이를 준다(표에서는 행 높이를 늘리지 않는다).
        "min-h-9 w-full whitespace-pre-wrap rounded-sm px-1.5 py-1 text-left text-xs transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:hover:bg-transparent md:min-h-0",
        needsReview && "font-medium text-warning",
        !value && "text-muted-foreground",
      )}
    >
      {value || "비어 있음"}
    </button>
  );
}
