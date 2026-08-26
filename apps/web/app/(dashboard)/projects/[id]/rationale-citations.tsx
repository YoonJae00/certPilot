"use client";

/**
 * 판정 본문의 인라인 인용 렌더러.
 *
 * 서버는 판정 근거·예상 결함·개선 권고 본문 안에 `(chunk:c_<uuid>)`, `evidence:e_<uuid>`
 * 형태로 근거를 인용한다(apps/api/app/workers/assess.py `_INLINE_REFERENCE_RE`).
 * uuid 를 그대로 보여 주면 문장을 읽을 수 없으므로 각주 번호 칩으로 치환하고,
 * 호버하면 출처 미리보기를, 누르면 아래 근거 카드로 스크롤·플래시를 준다.
 *
 * 주의: 본문 참조에는 `c_`/`e_` 접두사가 붙지만 API 가 주는 chunk_id·evidence_id 는
 * 접두사가 없는 uuid 다. 양쪽을 `normalizeRefId` 로 맞춰 짝짓는다.
 */

import { CircleHelp, Cloud, FileText } from "lucide-react";
import * as React from "react";

import { formatDateTime } from "@/lib/labels";
import type { FindingChunk, FindingEvidence } from "@/lib/types";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ */
/* 파싱                                                                */
/* ------------------------------------------------------------------ */

export type CitationKind = "chunk" | "evidence";

/** 본문에서 뽑아낸 인용 참조 1건. */
interface CitationRef {
  /** 원본 토큰(예: `chunk:c_a14…`). 미해결 칩의 title 로 쓴다. */
  token: string;
  kind: CitationKind;
  /** 접두사를 벗긴 id. chunks/evidence 목록과 짝지을 때 쓰는 키. */
  key: string;
}

/** 본문을 자른 조각. 텍스트 아니면 인용 묶음. */
type CitedNode =
  | { type: "text"; value: string }
  | { type: "citation"; refs: CitationRef[] };

/** 인용 토큰 1개. */
const REF_SOURCE = String.raw`\b(?:chunk|evidence):[ce]_[0-9A-Za-z-]+`;
/** 쉼표로 이어 붙인 토큰 목록(예: `chunk:c_x, evidence:e_y`). */
const REF_LIST_SOURCE = String.raw`${REF_SOURCE}(?:\s*[,;]\s*${REF_SOURCE})*`;

/**
 * 인용 묶음 패턴.
 *
 * 괄호 안이 토큰·쉼표·공백뿐이면 괄호째 삼킨다. 칩으로 바꾼 뒤 빈 `()` 나 쉼표
 * 찌꺼기가 남지 않게 하기 위해서다. 괄호 없이 문장에 박힌 토큰도 잡는다.
 * `lastIndex` 를 공유하지 않도록 호출마다 새로 만든다.
 */
function citationPattern(): RegExp {
  return new RegExp(
    String.raw`\(\s*${REF_LIST_SOURCE}\s*\)|\[\s*${REF_LIST_SOURCE}\s*\]|${REF_SOURCE}`,
    "g",
  );
}

/**
 * `c_`/`e_` 접두사를 벗기고 uuid 를 정규화한다(소문자·하이픈 제거).
 *
 * 서버 검증(`_normalize_reference`)은 `uuid.UUID` 파싱이라 대문자 hex,
 * 하이픈 없는 32-hex, 후행 하이픈이 붙은 토큰까지 통과시킨다. 같은 범위를
 * 받아 주도록 API 의 chunk_id·evidence_id 와 본문 참조 양쪽에 적용한다.
 */
export function normalizeRefId(value: string): string {
  return value
    .replace(/^[ce]_/, "")
    .replace(/-/g, "")
    .toLowerCase();
}

/** 근거 카드에 다는 DOM id. 칩이 이 id 로 카드를 찾아 스크롤한다. */
export function citationAnchorId(rawId: string): string {
  return `ref-card-${normalizeRefId(rawId)}`;
}

/** `chunk:c_x` 토큰 1개를 참조로 푼다. */
function toRef(token: string): CitationRef {
  const colon = token.indexOf(":");
  const kind: CitationKind = token.slice(0, colon) === "chunk" ? "chunk" : "evidence";
  return { token, kind, key: normalizeRefId(token.slice(colon + 1)) };
}

/** 묶음 문자열 안의 토큰을 등장 순서대로 뽑는다. */
function extractRefs(fragment: string): CitationRef[] {
  const pattern = new RegExp(REF_SOURCE, "g");
  const refs: CitationRef[] = [];
  for (;;) {
    const match = pattern.exec(fragment);
    if (match === null) break;
    refs.push(toRef(match[0]));
  }
  return refs;
}

/** 본문을 텍스트/인용 조각으로 자른다. 순수 함수 — 컴포넌트에서 useMemo 로 캐시한다. */
function parseCitedText(text: string): CitedNode[] {
  const pattern = citationPattern();
  const nodes: CitedNode[] = [];
  let cursor = 0;

  for (;;) {
    const match = pattern.exec(text);
    if (match === null) break;
    if (match.index > cursor) {
      nodes.push({ type: "text", value: text.slice(cursor, match.index) });
    }
    nodes.push({ type: "citation", refs: extractRefs(match[0]) });
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) {
    nodes.push({ type: "text", value: text.slice(cursor) });
  }
  return nodes;
}

/* ------------------------------------------------------------------ */
/* 번호 색인                                                           */
/* ------------------------------------------------------------------ */

/** 번호가 매겨진 근거 1건. 본문 칩과 근거 카드가 이 번호를 공유한다. */
export interface CitationTarget {
  key: string;
  kind: CitationKind;
  /** 본문 첫 등장 순서로 매긴 각주 번호(1부터). */
  number: number;
  chunk: FindingChunk | null;
  evidence: FindingEvidence | null;
}

/**
 * 본문 등장 순서로 번호를 매긴다.
 *
 * 청크·증적을 가리지 않는 전역 번호이고, 같은 참조가 여러 번 나오면 같은 번호다.
 * 본문에 인용되지 않은 근거는 인용된 번호 다음부터 목록 순서로 이어 붙인다.
 */
function buildCitationIndex(
  source: string,
  chunks: FindingChunk[],
  evidence: FindingEvidence[],
): Map<string, CitationTarget> {
  const chunkByKey = new Map<string, FindingChunk>();
  for (const chunk of chunks) {
    chunkByKey.set(normalizeRefId(chunk.chunk_id), chunk);
  }
  const evidenceByKey = new Map<string, FindingEvidence>();
  for (const item of evidence) {
    evidenceByKey.set(normalizeRefId(item.evidence_id), item);
  }

  const targets = new Map<string, CitationTarget>();
  const add = (key: string): void => {
    if (targets.has(key)) return;
    const chunk = chunkByKey.get(key);
    if (chunk !== undefined) {
      targets.set(key, {
        key,
        kind: "chunk",
        number: targets.size + 1,
        chunk,
        evidence: null,
      });
      return;
    }
    const item = evidenceByKey.get(key);
    if (item !== undefined) {
      targets.set(key, {
        key,
        kind: "evidence",
        number: targets.size + 1,
        chunk: null,
        evidence: item,
      });
    }
    // 목록에 없는 참조는 번호를 주지 않는다 — 미해결 칩으로 그려진다.
  };

  const pattern = new RegExp(REF_SOURCE, "g");
  for (;;) {
    const match = pattern.exec(source);
    if (match === null) break;
    add(toRef(match[0]).key);
  }
  for (const chunk of chunks) add(normalizeRefId(chunk.chunk_id));
  for (const item of evidence) add(normalizeRefId(item.evidence_id));

  return targets;
}

/* ------------------------------------------------------------------ */
/* 컨텍스트                                                            */
/* ------------------------------------------------------------------ */

interface CitationContextValue {
  resolve: (rawId: string) => CitationTarget | null;
  /** 해당 근거 카드로 스크롤하고 잠깐 강조한다. */
  navigate: (key: string) => void;
  /** 지금 강조 중인 근거 키. */
  flashKey: string | null;
}

/** Provider 밖에서 쓰이면 칩은 모두 미해결로 떨어진다(화면이 깨지지는 않는다). */
const INERT: CitationContextValue = {
  resolve: () => null,
  navigate: () => undefined,
  flashKey: null,
};

const CitationContext = React.createContext<CitationContextValue>(INERT);

/** 플래시 강조 지속 시간(ms). */
const FLASH_MS = 1600;

export function CitationProvider({
  chunks,
  evidence,
  texts,
  children,
}: {
  chunks: FindingChunk[];
  evidence: FindingEvidence[];
  /** 번호를 매길 본문들. 화면에 보이는 순서대로 준다. */
  texts: (string | null | undefined)[];
  children: React.ReactNode;
}) {
  // 배열 그대로는 매 렌더 새 참조라 문자열로 접어서 memo 키로 쓴다.
  const source = texts.map((text) => text ?? "").join("\n");
  const targets = React.useMemo(
    () => buildCitationIndex(source, chunks, evidence),
    [chunks, evidence, source],
  );

  // nonce 는 같은 칩을 연달아 눌러도 플래시가 다시 걸리게 한다.
  const [flash, setFlash] = React.useState<{
    key: string;
    nonce: number;
  } | null>(null);

  React.useEffect(() => {
    if (flash === null) return;
    const timer = window.setTimeout(() => setFlash(null), FLASH_MS);
    return () => window.clearTimeout(timer);
  }, [flash]);

  const navigate = React.useCallback((key: string) => {
    const card = document.getElementById(citationAnchorId(key));
    card?.scrollIntoView({ behavior: "smooth", block: "center" });
    setFlash((prev) => ({ key, nonce: (prev?.nonce ?? 0) + 1 }));
  }, []);

  const value = React.useMemo<CitationContextValue>(
    () => ({
      resolve: (rawId) => targets.get(normalizeRefId(rawId)) ?? null,
      navigate,
      flashKey: flash?.key ?? null,
    }),
    [flash, navigate, targets],
  );

  return (
    <CitationContext.Provider value={value}>{children}</CitationContext.Provider>
  );
}

/**
 * 근거 카드에 붙일 속성. 칩이 찾아올 DOM id 와 플래시 강조 클래스를 준다.
 *
 * ```tsx
 * const anchor = useCitationAnchor(chunk.chunk_id);
 * <Card id={anchor.id} className={anchor.className}>
 * ```
 */
export function useCitationAnchor(rawId: string): {
  id: string;
  className: string;
} {
  const { flashKey } = React.useContext(CitationContext);
  const key = normalizeRefId(rawId);
  return {
    id: citationAnchorId(key),
    className: cn(
      "scroll-mt-6 transition-shadow duration-200",
      flashKey === key &&
        "ring-2 ring-warning ring-offset-2 ring-offset-background",
    ),
  };
}

/* ------------------------------------------------------------------ */
/* 칩·팝오버                                                           */
/* ------------------------------------------------------------------ */

const CHIP_BASE =
  "inline-flex h-[18px] items-center gap-0.5 rounded-full border px-1.5 align-middle text-[11px] font-medium leading-none tabular-nums";

const CHIP_TINT: Record<CitationKind, string> = {
  chunk: "border-warning/50 bg-warning/20 text-foreground",
  evidence: "border-primary/40 bg-primary/10 text-primary",
};

const CHIP_HOVER: Record<CitationKind, string> = {
  chunk: "hover:bg-warning/40",
  evidence: "hover:bg-primary/20",
};

/** 팝오버 고정 폭(w-72)과 뷰포트 여백. 위치 계산에 픽셀 값이 필요하다. */
const POPOVER_WIDTH = 288;
const POPOVER_MARGIN = 8;
const POPOVER_GAP = 6;
/** 아래 공간이 이보다 좁으면 칩 위로 뒤집는다. */
const POPOVER_FLIP_BELOW = 160;

/** 팝오버를 하나만 띄우기 위한 신호. 새로 열리는 칩이 자기 id 를 실어 보낸다. */
const POPOVER_EVENT = "certpilot:citation-popover";

interface PopoverPosition {
  left: number;
  top: number | undefined;
  bottom: number | undefined;
}

/**
 * 칩 기준 팝오버 좌표.
 *
 * SheetContent 가 overflow-y-auto 라 absolute 로는 잘린다. 그래서 fixed 로 띄우고
 * 열릴 때마다 칩의 뷰포트 좌표를 다시 잰다.
 */
function measurePopover(chip: HTMLElement): PopoverPosition {
  const rect = chip.getBoundingClientRect();
  const width = Math.min(POPOVER_WIDTH, window.innerWidth - POPOVER_MARGIN * 2);
  const maxLeft = Math.max(
    POPOVER_MARGIN,
    window.innerWidth - width - POPOVER_MARGIN,
  );
  const left = Math.min(Math.max(rect.left, POPOVER_MARGIN), maxLeft);
  const spaceBelow = window.innerHeight - rect.bottom;

  if (spaceBelow < POPOVER_FLIP_BELOW && rect.top > spaceBelow) {
    return {
      left,
      top: undefined,
      bottom: window.innerHeight - rect.top + POPOVER_GAP,
    };
  }
  return { left, top: rect.bottom + POPOVER_GAP, bottom: undefined };
}

/** 칩의 접근성 라벨. 어디로 이동하는지까지 읽어 준다. */
function chipLabel(target: CitationTarget): string {
  if (target.chunk !== null) {
    const page = target.chunk.page;
    return page === null
      ? `근거 ${target.number} — ${target.chunk.filename} 근거로 이동`
      : `근거 ${target.number} — ${target.chunk.filename} ${page}쪽으로 이동`;
  }
  if (target.evidence !== null) {
    return `근거 ${target.number} — ${target.evidence.check_id} 증적으로 이동`;
  }
  return `근거 ${target.number}으로 이동`;
}

function CitationIcon({ kind }: { kind: CitationKind }) {
  const Icon = kind === "chunk" ? FileText : Cloud;
  return <Icon className="h-3 w-3 shrink-0" aria-hidden />;
}

/** 본문 안의 인용 칩 1개. 호버·포커스로 미리보기, 클릭으로 근거 카드 이동. */
function CitationChip({ reference }: { reference: CitationRef }) {
  const { resolve, navigate } = React.useContext(CitationContext);
  const target = resolve(reference.key);
  const chipRef = React.useRef<HTMLButtonElement>(null);
  const chipId = React.useId();
  const previewId = `${chipId}preview`;
  const [position, setPosition] = React.useState<PopoverPosition | null>(null);

  const close = React.useCallback(() => setPosition(null), []);

  React.useEffect(() => {
    if (position === null) return;
    const closeIfOther = (event: Event) => {
      if ((event as CustomEvent<string>).detail !== chipId) close();
    };
    // 스크롤하면 fixed 팝오버가 칩과 어긋나므로 그냥 닫는다(capture 로 내부 스크롤까지).
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    window.addEventListener(POPOVER_EVENT, closeIfOther);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
      window.removeEventListener(POPOVER_EVENT, closeIfOther);
    };
  }, [chipId, close, position]);

  const open = React.useCallback(() => {
    const chip = chipRef.current;
    if (chip === null) return;
    setPosition(measurePopover(chip));
    // 리스너는 다음 렌더에 붙으므로 자기 팝오버는 이 신호에 닫히지 않는다.
    window.dispatchEvent(new CustomEvent(POPOVER_EVENT, { detail: chipId }));
  }, [chipId]);

  if (target === null) {
    // 서버 검증을 통과했다면 나오지 않지만, 목록에 없는 참조도 문장은 읽히게 둔다.
    return (
      <span
        title={reference.token}
        className={cn(
          CHIP_BASE,
          "mx-[1px] border-dashed border-border bg-muted text-muted-foreground",
        )}
      >
        <CircleHelp className="h-3 w-3 shrink-0" aria-hidden />
        <span className="sr-only">확인할 수 없는 근거 참조</span>
        <span aria-hidden>?</span>
      </span>
    );
  }

  return (
    <span className="inline-flex align-middle">
      <button
        ref={chipRef}
        type="button"
        aria-label={chipLabel(target)}
        aria-describedby={position === null ? undefined : previewId}
        // 터치는 열지 않는다 — iOS 는 mouseover 중 DOM 이 바뀌면 click 을 버려서
        // 첫 탭이 미리보기만 열고 끝난다. 탭은 곧장 클릭(카드 이동)으로 처리한다.
        onPointerEnter={(event) => {
          if (event.pointerType === "mouse") open();
        }}
        onPointerLeave={close}
        onFocus={open}
        onBlur={close}
        onKeyDown={(event) => {
          // 포커스를 옮기지 않고도 닫을 수 있어야 한다(WCAG 1.4.13).
          // Sheet 가 같은 Esc 로 닫히지 않게 전파를 막는다.
          if (event.key === "Escape" && position !== null) {
            event.stopPropagation();
            close();
          }
        }}
        onClick={() => {
          close();
          navigate(target.key);
        }}
        className={cn(
          CHIP_BASE,
          CHIP_TINT[target.kind],
          CHIP_HOVER[target.kind],
          "mx-[1px] cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
        )}
      >
        <CitationIcon kind={target.kind} />
        {target.number}
      </button>
      {position === null ? null : (
        <CitationPopover id={previewId} target={target} position={position} />
      )}
    </span>
  );
}

/**
 * 출처 미리보기.
 *
 * 본문 `<p>` 안에 그려지므로 블록 태그를 쓸 수 없다 — 전부 span 이다.
 * 마우스를 가로채지 않게 pointer-events-none 으로 둔다.
 */
function CitationPopover({
  id,
  target,
  position,
}: {
  id: string;
  target: CitationTarget;
  position: PopoverPosition;
}) {
  return (
    <span
      id={id}
      role="tooltip"
      className="pointer-events-none fixed z-50 w-72 max-w-[calc(100vw_-_16px)] rounded-md border bg-popover p-3 text-popover-foreground shadow-md"
      style={{ left: position.left, top: position.top, bottom: position.bottom }}
    >
      {target.chunk !== null ? <ChunkPreview chunk={target.chunk} /> : null}
      {target.evidence !== null ? (
        <EvidencePreview item={target.evidence} />
      ) : null}
    </span>
  );
}

function ChunkPreview({ chunk }: { chunk: FindingChunk }) {
  return (
    <>
      <span className="block text-xs">
        <span className="font-semibold text-foreground">{chunk.filename}</span>
        <span className="ml-1.5 text-muted-foreground">
          {chunk.page === null ? "페이지 정보 없음" : `${chunk.page}쪽`}
        </span>
      </span>
      <span className="mt-2 line-clamp-5 whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
        {chunk.text}
      </span>
    </>
  );
}

function EvidencePreview({ item }: { item: FindingEvidence }) {
  return (
    <>
      <span className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="font-semibold text-foreground">{item.check_id}</span>
        <span className="rounded border px-1.5 py-0.5 text-[10px] uppercase text-muted-foreground">
          {item.source}
        </span>
        <span className="rounded bg-secondary px-1.5 py-0.5 text-[10px] font-medium text-secondary-foreground">
          {item.status}
        </span>
      </span>
      <span className="mt-2 block text-[11px] text-muted-foreground">
        수집 {formatDateTime(item.collected_at)}
      </span>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* 공개 컴포넌트                                                       */
/* ------------------------------------------------------------------ */

/**
 * 인용 토큰을 각주 칩으로 바꿔 본문을 그린다.
 *
 * 인라인 노드만 내보내므로 감싸는 `<p>` 의 기존 스타일(whitespace-pre-wrap 등)은
 * 호출하는 쪽이 그대로 유지한다.
 */
export function CitedText({ text }: { text: string }) {
  const nodes = React.useMemo(() => parseCitedText(text), [text]);

  return (
    <>
      {nodes.map((node, index) =>
        node.type === "text" ? (
          <React.Fragment key={`text-${index}`}>{node.value}</React.Fragment>
        ) : (
          <span
            key={`cite-${index}`}
            className="inline-flex items-center gap-0.5 align-middle"
          >
            {node.refs.map((ref, position) => (
              <CitationChip key={`${index}-${position}`} reference={ref} />
            ))}
          </span>
        ),
      )}
    </>
  );
}

/** 근거 카드 좌상단의 번호 배지. 본문 칩과 같은 번호·같은 색을 쓴다. */
export function CitationNumberBadge({ id }: { id: string }) {
  const { resolve } = React.useContext(CitationContext);
  const target = resolve(id);
  if (target === null) return null;

  return (
    <span className={cn(CHIP_BASE, CHIP_TINT[target.kind])}>
      <CitationIcon kind={target.kind} />
      <span className="sr-only">근거 </span>
      {target.number}
    </span>
  );
}
