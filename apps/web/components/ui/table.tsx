import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * 가로 스크롤 어포던스.
 *
 * 배경 4겹으로 만든다. 카드색 덮개 2겹(`local`)은 내용과 함께 스크롤하고, 그림자 2겹
 * (`scroll`)은 컨테이너에 고정된다. 스크롤이 없으면 덮개가 그림자를 계속 가리고,
 * 스크롤하면 그쪽 덮개가 밀려나며 "이 방향에 더 있다"는 그림자가 드러난다.
 * 좁은 화면에서 표가 잘려 보일 때만 힌트가 나오므로 넓은 화면에는 흔적이 없다.
 */
const SCROLL_HINT_STYLE: React.CSSProperties = {
  backgroundImage: [
    "linear-gradient(to right, hsl(var(--card)), hsl(var(--card) / 0))",
    "linear-gradient(to left, hsl(var(--card)), hsl(var(--card) / 0))",
    "linear-gradient(to right, hsl(var(--foreground) / 0.14), hsl(var(--foreground) / 0))",
    "linear-gradient(to left, hsl(var(--foreground) / 0.14), hsl(var(--foreground) / 0))",
  ].join(", "),
  backgroundPosition: "left center, right center, left center, right center",
  backgroundSize: "28px 100%, 28px 100%, 14px 100%, 14px 100%",
  backgroundRepeat: "no-repeat",
  backgroundAttachment: "local, local, scroll, scroll",
}

const Table = React.forwardRef<
  HTMLTableElement,
  React.HTMLAttributes<HTMLTableElement>
>(({ className, ...props }, ref) => (
  <div
    className="relative w-full overflow-x-auto overscroll-x-contain [scrollbar-color:hsl(var(--border))_transparent] [scrollbar-width:thin] [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-border [&::-webkit-scrollbar]:h-1.5"
    style={SCROLL_HINT_STYLE}
  >
    <table
      ref={ref}
      className={cn("w-full caption-bottom text-sm", className)}
      {...props}
    />
  </div>
))
Table.displayName = "Table"

const TableHeader = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <thead ref={ref} className={cn("[&_tr]:border-b", className)} {...props} />
))
TableHeader.displayName = "TableHeader"

const TableBody = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <tbody
    ref={ref}
    className={cn("[&_tr:last-child]:border-0", className)}
    {...props}
  />
))
TableBody.displayName = "TableBody"

const TableFooter = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <tfoot
    ref={ref}
    className={cn(
      "border-t bg-muted/50 font-medium [&>tr]:last:border-b-0",
      className
    )}
    {...props}
  />
))
TableFooter.displayName = "TableFooter"

const TableRow = React.forwardRef<
  HTMLTableRowElement,
  React.HTMLAttributes<HTMLTableRowElement>
>(({ className, ...props }, ref) => (
  <tr
    ref={ref}
    className={cn(
      "border-b transition-colors hover:bg-muted/50 data-[state=selected]:bg-muted",
      className
    )}
    {...props}
  />
))
TableRow.displayName = "TableRow"

const TableHead = React.forwardRef<
  HTMLTableCellElement,
  React.ThHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
  <th
    ref={ref}
    className={cn(
      "h-10 px-2 text-left align-middle font-medium text-muted-foreground [&:has([role=checkbox])]:pr-0 [&>[role=checkbox]]:translate-y-[2px]",
      className
    )}
    {...props}
  />
))
TableHead.displayName = "TableHead"

const TableCell = React.forwardRef<
  HTMLTableCellElement,
  React.TdHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
  <td
    ref={ref}
    className={cn(
      "p-2 align-middle [&:has([role=checkbox])]:pr-0 [&>[role=checkbox]]:translate-y-[2px]",
      className
    )}
    {...props}
  />
))
TableCell.displayName = "TableCell"

const TableCaption = React.forwardRef<
  HTMLTableCaptionElement,
  React.HTMLAttributes<HTMLTableCaptionElement>
>(({ className, ...props }, ref) => (
  <caption
    ref={ref}
    className={cn("mt-4 text-sm text-muted-foreground", className)}
    {...props}
  />
))
TableCaption.displayName = "TableCaption"

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
}
