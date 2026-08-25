"""문서 코파일럿 공용 도우미 (PRD §7 F4).

운영명세서(`draft_sow`)와 정책 초안(`draft_policy`)이 함께 쓰는 상수·정리 함수만 둔다.

이 계층의 원칙: **초안에 새로운 사실을 만들어 넣지 않는다.** 운영 현황 문장은 판정
(`findings`)의 rationale·predicted_defect·recommendation 과 근거 청크 본문만 재료로
쓰고, 채울 수 없는 칸은 `[확인 필요]` 로 남긴다(PRD §7 F4, 데모 기준 D3).
"""

import re
from typing import Any

# 채우지 못한 칸에 남기는 표시. 화면·DOCX·통계가 모두 이 문자열을 기준으로 센다.
NEEDS_REVIEW = "[확인 필요]"

# rationale 본문에 남아 있는 근거 인용. 사람이 읽는 문서에서는 지우고, 대신
# "관련 문서·증적" 칸에 사람이 읽을 수 있는 표시로 다시 넣는다.
_INLINE_CITATION_GROUP_RE = re.compile(
    r"\s*\((?:\s*(?:chunk|evidence):[ce]_[0-9A-Za-z-]+\s*[,、]?\s*)+\)"
)
_INLINE_CITATION_RE = re.compile(r"\s*\b(?:chunk|evidence):[ce]_[0-9A-Za-z-]+")

# 파일명 앞에 붙는 정렬용 번호(`01_`, `2-`)와 확장자. 표시용으로만 떼어 낸다.
_FILENAME_PREFIX_RE = re.compile(r"^\d{1,3}[._\-\s]+")
_WHITESPACE_RE = re.compile(r"\s+")


class DraftSourceError(RuntimeError):
    """초안을 만들 재료가 없다. API 계층이 400 으로 바꾼다."""


def strip_citations(text: str) -> str:
    """판정 근거 문장에서 `chunk:c_…` 형태의 내부 식별자 인용을 지운다."""
    cleaned = _INLINE_CITATION_GROUP_RE.sub("", text or "")
    cleaned = _INLINE_CITATION_RE.sub("", cleaned)
    # 인용을 지우면 괄호 안이 비거나 공백이 두 칸 남는다.
    cleaned = cleaned.replace("()", "")
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def as_sentence(text: str) -> str:
    """문장 끝에 마침표를 보장한다(문장을 이어 붙일 때 쓴다)."""
    stripped = (text or "").strip()
    if not stripped:
        return ""
    # 말줄임표로 끝나는 발췌 인용에는 마침표를 덧붙이지 않는다.
    if stripped[-1] in ".!?。…":
        return stripped
    return f"{stripped}."


def join_sentences(*parts: str) -> str:
    """빈 조각을 버리고 문장 단위로 이어 붙인다."""
    return " ".join(as_sentence(part) for part in parts if (part or "").strip())


def document_label(filename: str) -> str:
    """파일명을 문서 표시명으로 바꾼다.

    `01_정보보호정책_v2.1.pdf` → `정보보호정책 v2.1`. 정렬용 번호와 확장자를 떼고
    밑줄을 공백으로 바꾸기만 한다(내용을 새로 만들지 않는다).
    """
    name = (filename or "").strip()
    if not name:
        return NEEDS_REVIEW
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = _FILENAME_PREFIX_RE.sub("", stem)
    stem = stem.replace("_", " ")
    stem = _WHITESPACE_RE.sub(" ", stem).strip()
    return stem or name


def count_needs_review(value: Any) -> int:
    """중첩된 값 안에서 `[확인 필요]` 가 들어간 **칸(문자열)** 수를 센다.

    리스트 안의 문자열도 각각 한 칸으로 센다. 한 칸에 표시가 여러 번 있어도 1로 센다
    (사람이 채워야 할 칸의 개수가 의미 있는 숫자다).
    """
    if isinstance(value, str):
        return 1 if NEEDS_REVIEW in value else 0
    if isinstance(value, dict):
        return sum(count_needs_review(item) for item in value.values())
    if isinstance(value, list):
        return sum(count_needs_review(item) for item in value)
    return 0
