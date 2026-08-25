"use client";

/**
 * 승인·반려 확인 다이얼로그.
 *
 * 승인은 고객 다운로드를 여는 되돌릴 수 없는 결정이라 한 번 더 묻는다.
 * 반려는 코멘트가 있어야만 보낼 수 있다(무엇을 고쳐야 하는지 없이 되돌리지 않는다).
 */

import * as React from "react";

import { ReviewTextarea } from "@/app/(dashboard)/review/textarea";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";

interface ApproveDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 초안 이름(예: "운영명세서 v1"). */
  title: string;
  /** 아직 `[확인 필요]` 로 남은 칸 수. 0보다 크면 경고를 띄운다. */
  needsReview: number;
  pending: boolean;
  onConfirm: (comment: string) => void;
}

export function ApproveDialog({
  open,
  onOpenChange,
  title,
  needsReview,
  pending,
  onConfirm,
}: ApproveDialogProps) {
  const [comment, setComment] = React.useState("");

  React.useEffect(() => {
    if (!open) setComment("");
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>초안을 승인할까요?</DialogTitle>
          <DialogDescription>
            {title} 을(를) 승인하면 고객사가 곧바로 문서를 내려받을 수 있습니다.
            승인 후에는 이 과제를 다시 결정할 수 없습니다.
          </DialogDescription>
        </DialogHeader>

        {needsReview > 0 ? (
          <p
            role="alert"
            className="rounded-md bg-warning/10 px-3 py-2 text-sm text-warning"
          >
            아직 확인이 필요한 칸이 {needsReview}개 남아 있습니다.
          </p>
        ) : null}

        <div className="space-y-2">
          <Label htmlFor="approve-comment">검수 의견 (선택)</Label>
          <ReviewTextarea
            id="approve-comment"
            rows={3}
            value={comment}
            disabled={pending}
            placeholder="승인과 함께 남길 의견이 있으면 적어 주세요."
            onChange={(event) => setComment(event.target.value)}
          />
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={pending}
          >
            취소
          </Button>
          <Button onClick={() => onConfirm(comment.trim())} disabled={pending}>
            {pending ? "승인 중…" : "승인"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface ReturnDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  pending: boolean;
  onConfirm: (comment: string) => void;
}

export function ReturnDialog({
  open,
  onOpenChange,
  title,
  pending,
  onConfirm,
}: ReturnDialogProps) {
  const [comment, setComment] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) {
      setComment("");
      setError(null);
    }
  }, [open]);

  function handleConfirm() {
    const trimmed = comment.trim();
    if (!trimmed) {
      setError("반려 사유를 입력해 주세요.");
      return;
    }
    onConfirm(trimmed);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>초안을 반려할까요?</DialogTitle>
          <DialogDescription>
            {title} 을(를) 반려하면 고객사에 사유가 알림으로 전달되고, 보완 후 새 버전으로
            다시 제출됩니다.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="return-comment">반려 사유 (필수)</Label>
          <ReviewTextarea
            id="return-comment"
            rows={4}
            value={comment}
            disabled={pending}
            placeholder="어떤 부분을 어떻게 보완해야 하는지 적어 주세요."
            onChange={(event) => {
              setComment(event.target.value);
              if (error) setError(null);
            }}
          />
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={pending}
          >
            취소
          </Button>
          <Button
            variant="destructive"
            onClick={handleConfirm}
            disabled={pending}
          >
            {pending ? "반려 중…" : "반려"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
