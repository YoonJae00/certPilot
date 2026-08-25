"use client";

/** 정책 초안 검수 화면. 조항마다 본문 textarea 를 그대로 연다(제목은 템플릿 값이라 고정). */

import * as React from "react";

import { ReviewTextarea } from "@/app/(dashboard)/review/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { NEEDS_REVIEW } from "@/lib/api-review";
import { cn } from "@/lib/utils";
import type { PolicySection } from "@/lib/types-review";

interface PolicyEditorProps {
  sections: PolicySection[];
  /** 저장되지 않은 변경이 있는 조항 번호. */
  dirtySections: ReadonlySet<number>;
  disabled: boolean;
  onChange: (sectionIndex: number, body: string) => void;
}

export function PolicyEditor({
  sections,
  dirtySections,
  disabled,
  onChange,
}: PolicyEditorProps) {
  return (
    <div className="space-y-4">
      {sections.map((section, index) => (
        <Card
          key={`${section.heading}-${index}`}
          className={cn(dirtySections.has(index) && "border-warning")}
        >
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              {section.heading}
              {section.body.includes(NEEDS_REVIEW) ? (
                <span className="text-xs font-medium text-warning">
                  {NEEDS_REVIEW}
                </span>
              ) : null}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ReviewTextarea
              aria-label={`${section.heading} 본문`}
              value={section.body}
              rows={Math.min(14, Math.max(4, section.body.split("\n").length + 1))}
              disabled={disabled}
              onChange={(event) => onChange(index, event.target.value)}
            />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
