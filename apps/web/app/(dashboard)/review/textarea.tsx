"use client";

/**
 * 검수 화면 전용 여러 줄 입력.
 *
 * shadcn 기본 세트에 textarea 가 아직 없어서, Input 과 같은 토큰으로 여기서만 만든다.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

export const ReviewTextarea = React.forwardRef<
  HTMLTextAreaElement,
  React.ComponentProps<"textarea">
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
      className,
    )}
    {...props}
  />
));
ReviewTextarea.displayName = "ReviewTextarea";
