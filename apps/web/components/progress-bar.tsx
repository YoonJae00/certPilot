/**
 * 진행률·준비도 표시용 막대.
 *
 * shadcn 의 progress 는 `@radix-ui/react-progress` 의존이 필요해,
 * 새 의존성 없이 같은 접근성 속성(role="progressbar")을 갖는 최소 구현을 쓴다.
 */

import { cn } from "@/lib/utils";

export function ProgressBar({
  /** 0~100 백분율. 범위를 벗어나면 잘라낸다. */
  value,
  label,
  className,
  barClassName,
}: {
  value: number | null;
  /** 스크린리더용 설명. 시각적으로는 노출하지 않는다. */
  label: string;
  className?: string;
  barClassName?: string;
}) {
  const percent =
    value === null ? 0 : Math.min(100, Math.max(0, Math.round(value)));

  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value === null ? undefined : percent}
      className={cn(
        "h-2 w-full overflow-hidden rounded-full bg-secondary",
        className,
      )}
    >
      <div
        className={cn(
          "h-full rounded-full bg-primary transition-[width] duration-500",
          barClassName,
        )}
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}
