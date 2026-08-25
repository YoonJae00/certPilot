"use client";

/**
 * 운영명세서 검수 표(101행).
 *
 * 운영 현황·담당 부서·비고 칸만 편집할 수 있다. 항목 코드·항목명·관련 문서는 지식베이스와
 * 판정에서 온 값이라 심사원이 바꾸지 않는다(내용을 지어내지 않는다는 원칙).
 *
 * 칸을 클릭하면 그 자리에서 입력으로 바뀌고, 실제 저장은 하단 바의 "저장" 버튼이 한 번에
 * 보낸다(101행을 칸마다 저장하면 요청이 폭증하고 DOCX 재생성도 그만큼 돈다).
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

export function SowEditor({
  rows,
  dirtyRows,
  disabled,
  onChange,
}: SowEditorProps) {
  const [editing, setEditing] = React.useState<CellPosition | null>(null);

  function isEditing(row: number, field: SowEditableField): boolean {
    return editing?.row === row && editing.field === field;
  }

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[84px]">코드</TableHead>
            <TableHead className="w-[180px]">항목명</TableHead>
            <TableHead className="min-w-[320px]">운영 현황</TableHead>
            <TableHead className="w-[180px]">관련 문서·증적</TableHead>
            <TableHead className="w-[140px]">담당 부서</TableHead>
            <TableHead className="w-[180px]">비고</TableHead>
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
              <TableCell className="align-top text-xs">
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
        "w-full whitespace-pre-wrap rounded-sm px-1.5 py-1 text-left text-xs transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:hover:bg-transparent",
        needsReview && "font-medium text-warning",
        !value && "text-muted-foreground",
      )}
    >
      {value || "비어 있음"}
    </button>
  );
}
