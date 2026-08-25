"""ISMS-P 인증기준 안내서 PDF → data/criteria/criteria.json 빌더.

공식 안내서(2023.11 개정)의 제2장 "인증기준 설명"을 파싱해 101개 항목의
인증기준·주요 확인사항·증거자료·결함사례를 추출한다.

본문은 전부 PDF에서 추출한 텍스트이며, 이 스크립트는 어떤 문장도 생성하지 않는다.

실행:
    cd apps/api && uv run python ../../scripts/kb_build.py
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber
from pypdf import PdfReader

# --- 경로 (리포 루트 기준으로 계산한다) ---
REPO_ROOT = Path(__file__).resolve().parent.parent
PDF_PATH = REPO_ROOT / "data" / "raw" / "isms-p-guide-2023-11.pdf"
CRITERIA_DIR = REPO_ROOT / "data" / "criteria"
OUT_PATH = CRITERIA_DIR / "criteria.json"
REPORT_PATH = CRITERIA_DIR / "build_report.md"
SIMPLIFIED_PATH = CRITERIA_DIR / "simplified_codes.txt"

KB_VERSION = "2023"
KB_SOURCE = (
    "개인정보보호위원회·과학기술정보통신부, "
    "ISMS-P 인증기준 안내서(2023.11), isms-p-guide-2023-11.pdf"
)

# --- 레이아웃 상수 (612x859 페이지 기준, 실측값) ---
# 좌우 여백과 세로 사이드탭(x>=587), 머리글/바닥글(top>=775)을 잘라낸다.
CROP_LEFT = 0.0
CROP_TOP = 105.0
CROP_RIGHT = 578.0
CROP_BOTTOM = 772.0
# 표의 라벨 칸(인증기준·주요 확인사항 등)은 x 110.9~180.8, 본문 칸은 x 186.4~ 이다.
LABEL_COLUMN_MAX_X = 183.0
# 라벨 칸 사각형(표 행 경계)을 고르는 범위.
LABEL_RECT_X0_MAX = 130.0
LABEL_RECT_X1_MIN = 160.0
LABEL_RECT_X1_MAX = 200.0
LABEL_RECT_MIN_HEIGHT = 8.0
# 같은 시각적 줄로 묶을 세로 허용 오차 (줄 간격은 13pt 이상).
LINE_TOLERANCE = 6.0

# 안내서가 쓰는 심볼 폰트 불릿 글리프.
BULLET_CHARS = "\uf09f\uf06e\uf0ab\u25aa\u2022\u25cf\u25a0"
# 심볼 폰트의 공백 글리프.
SPACE_GLYPHS = "\uf020"

# --- 섹션 라벨 (공백 제거 후 비교) ---
L_ITEM = "항목"
L_REQUIREMENT = "인증기준"
L_CHECKPOINTS = "주요확인사항"
L_LAWS = "관련법규"
L_DETAIL = "세부설명"
L_EVIDENCE = "증거자료"
L_DEFECTS = "결함사례"

SECTION_ORDER: dict[str, int] = {
    L_ITEM: 0,
    L_REQUIREMENT: 1,
    L_CHECKPOINTS: 2,
    L_LAWS: 3,
    L_DETAIL: 4,
    L_EVIDENCE: 5,
    L_DEFECTS: 6,
}
# "증거자료" 아래 붙는 부제로, 섹션 전환이 아니다.
IGNORED_LABELS = frozenset({"예시"})

LABEL_PATTERN = re.compile(
    r"^(항\s*목|인증기준|주요\s*확인사항|관련\s*법규|세부\s*설명|증거자료|결함사례)\s+(.*)$"
)
CODE_PATTERN = re.compile(r"^(\d\.\d{1,2}\.\d{1,2})\s+(.+)$")
CODE_ONLY_PATTERN = re.compile(r"^\d\.\d{1,2}\.\d{1,2}$")
# 목차 줄: "1.1. 관리체계 기반 마련 010"
TOC_PATTERN = re.compile(r"^(\d\.\d{1,2})\.\s+(.+?)\s+\d{3}$")
CASE_PATTERN = re.compile(r"(?=사례\s*\d+\s*[:：])")

# 마지막 항목(3.5.3) 뒤에 이어지는 부록. 여기서 항목 수집을 끝낸다.
STOP_MARKER = "참고자료"

TOC_PAGE_INDEX = 4  # PDF 5쪽 (0-based)
EXPECTED_TOTAL = 101
EXPECTED_PER_CHAPTER = {1: 16, 2: 64, 3: 21}


@dataclass
class Line:
    """페이지에서 뽑아낸 한 줄. 라벨 칸과 본문 칸을 분리해 둔다."""

    page: int
    label: str | None
    text: str
    bullet: bool


@dataclass
class RawItem:
    """항목 단위로 모은 원본 줄들."""

    code: str
    title: str
    page: int
    sections: dict[str, list[Line]] = field(default_factory=dict)


def normalize_label(text: str) -> str:
    """라벨 비교용으로 공백을 제거한다."""
    return re.sub(r"\s+", "", text)


def clean_text(text: str) -> str:
    """추출 노이즈(심볼 공백, 중복 공백)를 정리한다."""
    for glyph in SPACE_GLYPHS:
        text = text.replace(glyph, " ")
    for glyph in BULLET_CHARS:
        text = text.replace(glyph, " ")
    text = text.replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def is_bullet_token(text: str) -> bool:
    """불릿 글리프만으로 이루어진 토큰인지."""
    stripped = text.strip()
    return bool(stripped) and all(ch in BULLET_CHARS for ch in stripped)


def group_words_into_lines(
    words: Sequence[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """단어를 세로 위치 기준으로 시각적 줄로 묶는다."""
    lines: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_top = 0.0
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if current and word["top"] - current_top > LINE_TOLERANCE:
            lines.append(current)
            current = []
        if not current:
            current_top = word["top"]
        current.append(word)
    if current:
        lines.append(current)
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def find_label_rows(page: Any, warnings: list[str]) -> list[tuple[float, float, str]]:
    """표의 라벨 칸 사각형에서 (행 시작, 행 끝, 라벨)을 읽는다.

    안내서의 라벨은 셀 안에서 세로 가운데 정렬이라 텍스트 줄만 봐서는
    행 경계를 알 수 없다. 그래서 셀 사각형 좌표를 행 경계로 쓴다.
    """
    rows: list[tuple[float, float, str]] = []
    for rect in page.rects:
        if rect["x0"] > LABEL_RECT_X0_MAX:
            continue
        if not LABEL_RECT_X1_MIN < rect["x1"] < LABEL_RECT_X1_MAX:
            continue
        if rect["bottom"] - rect["top"] < LABEL_RECT_MIN_HEIGHT:
            continue
        try:
            cell = page.crop((rect["x0"], rect["top"], rect["x1"], rect["bottom"]))
            label = normalize_label(cell.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 - 셀 하나 실패는 건너뛴다
            warnings.append(f"PDF {page.page_number}쪽 라벨 셀 추출 실패: {exc}")
            continue
        if label in SECTION_ORDER:
            rows.append((rect["top"], rect["bottom"], label))
    rows.sort()
    return rows


def row_label_for(
    rows: Sequence[tuple[float, float, str]], top: float, bottom: float
) -> str | None:
    """줄의 세로 중심이 어느 표 행에 속하는지 찾는다."""
    center = (top + bottom) / 2
    for row_top, row_bottom, label in rows:
        if row_top <= center <= row_bottom:
            return label
    return None


def split_label(words: Sequence[dict[str, Any]]) -> tuple[str | None, list[str]]:
    """줄 전체가 알려진 라벨(세부 설명·증거자료·결함사례)인지 본다."""
    tokens = [w["text"] for w in words]
    joined = normalize_label("".join(tokens))
    if joined in SECTION_ORDER or joined in IGNORED_LABELS:
        return joined, []
    return None, tokens


def build_line(
    page_no: int,
    words: Sequence[dict[str, Any]],
    rows: Sequence[tuple[float, float, str]],
) -> Line | None:
    """단어 묶음 하나를 Line 으로 변환한다."""
    top = min(w["top"] for w in words)
    bottom = max(w["bottom"] for w in words)
    row_label = row_label_for(rows, top, bottom)
    if row_label is not None:
        # 표 행 안이면 라벨은 사각형이 알려주고, 본문 칸 단어만 남긴다.
        label = row_label
        tokens = [w["text"] for w in words if w["x0"] >= LABEL_COLUMN_MAX_X]
    else:
        label, tokens = split_label(words)

    bullet = False
    while tokens and is_bullet_token(tokens[0]):
        bullet = True
        tokens.pop(0)
    text = clean_text(" ".join(tokens))
    if label is None and not text:
        return None
    return Line(page=page_no, label=label, text=text, bullet=bullet)


def build_line_from_text(page_no: int, raw: str) -> Line | None:
    """좌표 정보가 없는 폴백 경로: 정규식으로 라벨을 뗀다."""
    text = clean_text(raw)
    if not text:
        return None
    bullet = bool(raw.strip()) and raw.strip()[0] in BULLET_CHARS
    label = None
    joined = normalize_label(text)
    if joined in SECTION_ORDER or joined in IGNORED_LABELS:
        return Line(page=page_no, label=joined, text="", bullet=bullet)
    match = LABEL_PATTERN.match(text)
    if match:
        label = normalize_label(match.group(1))
        text = match.group(2).strip()
    return Line(page=page_no, label=label, text=text, bullet=bullet)


def extract_lines(pdf_path: Path, warnings: list[str]) -> list[Line]:
    """PDF 전체에서 Line 목록을 만든다. 페이지 단위로 pypdf 폴백."""
    lines: list[Line] = []
    fallback_reader: PdfReader | None = None
    with pdfplumber.open(str(pdf_path)) as pdf:
        for index, page in enumerate(pdf.pages):
            page_no = index + 1
            try:
                rows = find_label_rows(page, warnings)
                cropped = page.crop(
                    (CROP_LEFT, CROP_TOP, CROP_RIGHT, min(CROP_BOTTOM, page.height))
                )
                words = cropped.extract_words()
                page_lines = [
                    line
                    for group in group_words_into_lines(words)
                    if (line := build_line(page_no, group, rows)) is not None
                ]
            except Exception as exc:  # noqa: BLE001 - 페이지 단위로 폴백한다
                warnings.append(f"PDF {page_no}쪽 pdfplumber 추출 실패 → pypdf 폴백: {exc}")
                if fallback_reader is None:
                    fallback_reader = PdfReader(str(pdf_path))
                raw = fallback_reader.pages[index].extract_text() or ""
                page_lines = [
                    line
                    for raw_line in raw.splitlines()
                    if (line := build_line_from_text(page_no, raw_line)) is not None
                ]
            lines.extend(page_lines)
    return lines


def parse_toc(pdf_path: Path, warnings: list[str]) -> dict[str, str]:
    """목차 페이지에서 분야명(2.5 인증 및 권한관리)을 읽는다."""
    sections: dict[str, str] = {}
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            raw = pdf.pages[TOC_PAGE_INDEX].extract_text() or ""
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"목차 페이지 추출 실패: {exc}")
        return sections
    for raw_line in raw.splitlines():
        match = TOC_PATTERN.match(clean_text(raw_line))
        if match:
            sections[match.group(1)] = f"{match.group(1)} {match.group(2)}"
    return sections


def collect_items(lines: Iterable[Line], warnings: list[str]) -> list[RawItem]:
    """Line 스트림을 항목 단위로 자르고 섹션별로 모은다."""
    items: list[RawItem] = []
    current: RawItem | None = None
    current_section: str | None = None
    current_order = -1
    pending_code: str | None = None

    for line in lines:
        if line.label is None and line.text.startswith(STOP_MARKER):
            break
        if line.label == L_ITEM:
            match = CODE_PATTERN.match(line.text)
            if match is None:
                # 코드와 제목이 다음 줄로 밀린 경우를 대비한다.
                if CODE_ONLY_PATTERN.match(line.text):
                    pending_code = line.text
                continue
            current = RawItem(code=match.group(1), title=match.group(2).strip(), page=line.page)
            items.append(current)
            current_section = L_ITEM
            current_order = 0
            pending_code = None
            continue

        if pending_code and line.label is None and line.text:
            current = RawItem(code=pending_code, title=line.text.strip(), page=line.page)
            items.append(current)
            current_section = L_ITEM
            current_order = 0
            pending_code = None
            continue

        if current is None:
            continue

        if line.label in IGNORED_LABELS:
            continue

        if line.label is not None:
            order = SECTION_ORDER[line.label]
            if order > current_order:
                current_section = line.label
                current_order = order
            elif line.label != current_section:
                warnings.append(
                    f"{current.code}: PDF {line.page}쪽에서 순서를 벗어난 "
                    f"'{line.label}' 라벨을 무시했다"
                )
        if current_section is None or current_section == L_ITEM:
            continue
        if not line.text and not line.bullet:
            continue
        current.sections.setdefault(current_section, []).append(line)

    return items


def join_lines(lines: Sequence[Line]) -> str:
    """줄바꿈으로 끊긴 문장을 한 줄로 잇는다."""
    return clean_text(" ".join(line.text for line in lines if line.text))


def split_bullets(lines: Sequence[Line]) -> list[str]:
    """불릿 표시를 기준으로 항목 목록을 만든다."""
    bullets: list[list[str]] = []
    for line in lines:
        if line.bullet or not bullets:
            bullets.append([])
        if line.text:
            bullets[-1].append(line.text)
    return [clean_text(" ".join(parts)) for parts in bullets if clean_text(" ".join(parts))]


def split_defect_cases(lines: Sequence[Line]) -> list[str]:
    """결함사례를 '사례 N :' 단위로 나눈다. 불릿이 깨지면 정규식으로 보정."""
    bullets = split_bullets(lines)
    result: list[str] = []
    for bullet in bullets:
        pieces = [piece.strip() for piece in CASE_PATTERN.split(bullet) if piece.strip()]
        result.extend(pieces if pieces else [bullet])
    return result


def to_criterion(
    item: RawItem,
    sections: dict[str, str],
    simplified: set[str],
    problems: list[str],
) -> dict[str, Any]:
    """RawItem 을 criteria.json 항목 dict 로 변환한다."""
    chapter = int(item.code.split(".")[0])
    section_key = ".".join(item.code.split(".")[:2])
    section = sections.get(section_key)
    if section is None:
        section = section_key
        problems.append(f"{item.code}: 목차에서 분야명 '{section_key}' 을 찾지 못했다")

    requirement = join_lines(item.sections.get(L_REQUIREMENT, []))
    checkpoints = split_bullets(item.sections.get(L_CHECKPOINTS, []))
    evidence_hints = split_bullets(item.sections.get(L_EVIDENCE, []))
    defect_examples = split_defect_cases(item.sections.get(L_DEFECTS, []))

    if len(requirement) <= 50:
        problems.append(
            f"{item.code}: 인증기준 본문이 {len(requirement)}자로 짧다 (PDF {item.page}쪽)"
        )
    if not checkpoints:
        problems.append(f"{item.code}: 주요 확인사항을 찾지 못했다 (PDF {item.page}쪽)")
    if not evidence_hints:
        problems.append(f"{item.code}: 증거자료 예시를 찾지 못했다 (PDF {item.page}쪽)")
    if not defect_examples:
        problems.append(f"{item.code}: 결함사례를 찾지 못했다 (PDF {item.page}쪽)")

    return {
        "code": item.code,
        "chapter": chapter,
        "section": section,
        "title": item.title,
        "requirement": requirement,
        "checkpoints": checkpoints,
        "defect_examples": defect_examples,
        "is_simplified": item.code in simplified,
        "evidence_hints": evidence_hints,
    }


def load_simplified_codes(path: Path) -> set[str]:
    """간편인증 항목 코드 목록을 읽는다. 주석(#)과 빈 줄은 건너뛴다."""
    if not path.exists():
        return set()
    codes: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            codes.add(line)
    return codes


def validate(criteria: Sequence[dict[str, Any]], problems: list[str]) -> None:
    """개수·중복 등 구조 검증 결과를 리포트에 남긴다."""
    if len(criteria) != EXPECTED_TOTAL:
        problems.append(f"항목 수가 {len(criteria)}개다 (기대 {EXPECTED_TOTAL}개)")
    per_chapter: dict[int, int] = {}
    for item in criteria:
        per_chapter[item["chapter"]] = per_chapter.get(item["chapter"], 0) + 1
    for chapter, expected in EXPECTED_PER_CHAPTER.items():
        actual = per_chapter.get(chapter, 0)
        if actual != expected:
            problems.append(f"제{chapter}장 항목이 {actual}개다 (기대 {expected}개)")
    seen: set[str] = set()
    for item in criteria:
        if item["code"] in seen:
            problems.append(f"코드 중복: {item['code']}")
        seen.add(item["code"])


def write_report(
    path: Path,
    criteria: Sequence[dict[str, Any]],
    warnings: Sequence[str],
    problems: Sequence[str],
) -> None:
    """파싱 실패·의심 항목을 마크다운 리포트로 남긴다."""
    per_chapter: dict[int, int] = {}
    for item in criteria:
        per_chapter[item["chapter"]] = per_chapter.get(item["chapter"], 0) + 1
    lines = [
        "# criteria.json 빌드 리포트",
        "",
        f"- 원본: `{PDF_PATH.relative_to(REPO_ROOT)}`",
        f"- 추출 항목 수: {len(criteria)} (기대 {EXPECTED_TOTAL})",
        "- 장별 항목 수: "
        + ", ".join(f"제{ch}장 {per_chapter.get(ch, 0)}개" for ch in sorted(EXPECTED_PER_CHAPTER)),
        "",
        "이 파일은 `make kb` 실행 시 자동 생성된다. 직접 고치지 말 것.",
        "",
        "## 구조 검증",
        "",
    ]
    lines.extend([f"- {p}" for p in problems] if problems else ["- 문제 없음"])
    lines.extend(["", "## 추출 경고", ""])
    lines.extend([f"- {w}" for w in warnings] if warnings else ["- 없음"])
    lines.extend(
        [
            "",
            "## 블록별 결측 현황",
            "",
            "| 코드 | 제목 | 인증기준(자) | 확인사항 | 증거자료 | 결함사례 |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    suspicious = [
        item
        for item in criteria
        if len(item["requirement"]) <= 50
        or not item["checkpoints"]
        or not item["evidence_hints"]
        or not item["defect_examples"]
    ]
    if suspicious:
        for item in suspicious:
            lines.append(
                f"| {item['code']} | {item['title']} | {len(item['requirement'])} | "
                f"{len(item['checkpoints'])} | {len(item['evidence_hints'])} | "
                f"{len(item['defect_examples'])} |"
            )
    else:
        lines.append("| - | 결측 항목 없음 | - | - | - | - |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    """빌드 진입점."""
    if not PDF_PATH.exists():
        print(f"안내서 PDF가 없다: {PDF_PATH}", file=sys.stderr)
        return 1
    CRITERIA_DIR.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    problems: list[str] = []

    sections = parse_toc(PDF_PATH, warnings)
    lines = extract_lines(PDF_PATH, warnings)
    raw_items = collect_items(lines, warnings)
    simplified = load_simplified_codes(SIMPLIFIED_PATH)

    criteria = [to_criterion(item, sections, simplified, problems) for item in raw_items]
    criteria.sort(key=lambda c: [int(part) for part in c["code"].split(".")])
    validate(criteria, problems)

    payload = {"version": KB_VERSION, "source": KB_SOURCE, "items": criteria}
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_report(REPORT_PATH, criteria, warnings, problems)

    print(f"항목 {len(criteria)}개 → {OUT_PATH.relative_to(REPO_ROOT)}")
    print(f"리포트 → {REPORT_PATH.relative_to(REPO_ROOT)} (문제 {len(problems)}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
