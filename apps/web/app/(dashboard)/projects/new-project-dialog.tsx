"use client";

/** 새 프로젝트 생성 다이얼로그. org_admin 에게만 노출된다. */

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { projectsApi, toMessage } from "@/lib/api";
import type { CertType, Project } from "@/lib/types";

interface NewProjectDialogProps {
  /** 생성 성공 시 새 프로젝트를 목록에 반영한다. */
  onCreated: (project: Project) => void;
}

export function NewProjectDialog({ onCreated }: NewProjectDialogProps) {
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [certType, setCertType] = React.useState<CertType>("ISMS-P");
  const [isSimplified, setIsSimplified] = React.useState(false);
  const [scopeText, setScopeText] = React.useState("");
  const [auditDueDate, setAuditDueDate] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [pending, setPending] = React.useState(false);

  function reset() {
    setName("");
    setCertType("ISMS-P");
    setIsSimplified(false);
    setScopeText("");
    setAuditDueDate("");
    setError(null);
    setPending(false);
  }

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (!next) reset();
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (!name.trim()) {
      setError("프로젝트 이름을 입력해 주세요.");
      return;
    }

    setPending(true);
    try {
      const project = await projectsApi.create({
        name: name.trim(),
        cert_type: certType,
        is_simplified: isSimplified,
        scope_text: scopeText.trim() ? scopeText.trim() : null,
        audit_due_date: auditDueDate ? auditDueDate : null,
      });
      onCreated(project);
      toast.success("프로젝트를 만들었습니다.");
      handleOpenChange(false);
    } catch (err) {
      setError(toMessage(err));
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button>새 프로젝트</Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>새 프로젝트</DialogTitle>
          <DialogDescription>
            인증 준비를 진행할 대상 범위를 등록합니다. 등록 후에도 수정할 수 있습니다.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4" noValidate>
          <div className="space-y-2">
            <Label htmlFor="project-name">프로젝트 이름</Label>
            <Input
              id="project-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="예: 2026 ISMS-P 최초심사"
              maxLength={200}
              disabled={pending}
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="cert-type">인증 유형</Label>
              <Select
                value={certType}
                onValueChange={(value) => setCertType(value as CertType)}
                disabled={pending}
              >
                <SelectTrigger id="cert-type">
                  <SelectValue placeholder="선택" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ISMS">ISMS</SelectItem>
                  <SelectItem value="ISMS-P">ISMS-P</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="audit-due-date">사후심사 예정일</Label>
              <Input
                id="audit-due-date"
                type="date"
                value={auditDueDate}
                onChange={(event) => setAuditDueDate(event.target.value)}
                disabled={pending}
              />
            </div>
          </div>

          <div className="flex items-start gap-3 rounded-md border p-3">
            <input
              id="is-simplified"
              type="checkbox"
              className="mt-1 h-4 w-4 rounded border-input accent-primary"
              checked={isSimplified}
              onChange={(event) => setIsSimplified(event.target.checked)}
              disabled={pending}
            />
            <div className="space-y-0.5">
              <Label htmlFor="is-simplified" className="cursor-pointer">
                간편인증 대상
              </Label>
              <p className="text-xs text-muted-foreground">
                간편인증 대상이면 축소된 인증 기준 세트를 적용합니다.
              </p>
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="scope-text">인증 범위 설명 (선택)</Label>
            <textarea
              id="scope-text"
              value={scopeText}
              onChange={(event) => setScopeText(event.target.value)}
              placeholder="예: 온라인 쇼핑몰 서비스 및 이를 지원하는 정보시스템"
              rows={3}
              disabled={pending}
              className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>

          {error ? (
            <p
              role="alert"
              className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              {error}
            </p>
          ) : null}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => handleOpenChange(false)}
              disabled={pending}
            >
              취소
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? "만드는 중…" : "만들기"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
