"""모의심사 프롬프트 조립과 LLM 응답 스키마.

프롬프트 본문은 `app/llm/prompts/assess.md` 에 있다(PRD §8 구조 그대로). 파일은
`<!-- system -->` / `<!-- user -->` 두 블록으로 나뉘고, 사용자 블록의 `{{자리표시자}}`
를 항목 값으로 치환한다. 본문에 JSON 예시가 들어 있어 중괄호가 흔하므로
`str.format` 대신 명시적 치환을 쓴다.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import FindingStatus

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "assess.md"

_SYSTEM_MARKER = "<!-- system -->"
_USER_MARKER = "<!-- user -->"

# 프롬프트에 넣는 청크 본문 최대 길이. 너무 길면 토큰만 먹고 판정에 도움이 안 된다.
CHUNK_SNIPPET_CHARS = 700

# 근거가 하나도 없을 때 `## 근거` 블록에 넣는 문구.
NO_EVIDENCE_TEXT = "근거 없음"


class PromptLoadError(RuntimeError):
    """프롬프트 파일이 없거나 형식이 어긋날 때."""


@dataclass(frozen=True)
class CriterionPrompt:
    """프롬프트에 넣을 인증기준 항목 스냅샷.

    ORM 객체를 스레드 사이로 넘기지 않기 위한 값 객체다. 본문은 항상
    `criteria` 테이블(=`data/criteria/criteria.json`)에서 온다.
    """

    code: str
    chapter: int
    section: str
    title: str
    requirement: str
    checkpoints: list[str] = field(default_factory=list)
    defect_examples: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ChunkRef:
    """프롬프트에 인용할 문서 청크."""

    id: uuid.UUID
    filename: str
    page: int | None
    text: str

    @property
    def reference(self) -> str:
        """참조 토큰(`c_<uuid>`)."""
        return f"c_{self.id}"

    def to_block(self) -> str:
        """`[chunk:c_… | 파일명 p.페이지] "본문"` 한 줄."""
        page = f" p.{self.page}" if self.page is not None else ""
        body = self.text.strip().replace("\n", " ")
        if len(body) > CHUNK_SNIPPET_CHARS:
            body = body[:CHUNK_SNIPPET_CHARS] + "…"
        return f'[chunk:{self.reference} | {self.filename}{page}] "{body}"'


@dataclass(frozen=True)
class EvidenceBlock:
    """프롬프트에 인용할 커넥터 증적."""

    id: uuid.UUID
    source: str
    check_id: str
    status: str
    collected_at: datetime
    summary: str

    @property
    def reference(self) -> str:
        """참조 토큰(`e_<uuid>`)."""
        return f"e_{self.id}"

    def to_block(self) -> str:
        """`[evidence:e_… | source.check_id | 수집시각] payload 요약` 한 줄."""
        collected = self.collected_at.strftime("%Y-%m-%d")
        return (
            f"[evidence:{self.reference} | {self.source}.{self.check_id} | {collected}] "
            f"{self.status}: {self.summary}"
        )


@dataclass(frozen=True)
class AssessPrompt:
    """조립된 프롬프트 한 쌍."""

    system: str
    user: str


class LLMFinding(BaseModel):
    """LLM 이 돌려줘야 하는 판정 JSON(PRD §6 판정 출력 스키마).

    여기를 통과하지 못한 응답은 폐기하고 재시도한다.
    """

    model_config = ConfigDict(extra="ignore")

    criterion_code: str
    status: FindingStatus
    confidence: float = 0.0
    rationale: str = ""
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    predicted_defect: str | None = None
    recommendation: str | None = None
    missing_info: list[str] = Field(default_factory=list)

    @field_validator("confidence", mode="after")
    @classmethod
    def _clamp_confidence(cls, value: float) -> float:
        """0~1 밖으로 나온 값은 잘라 준다(이 필드 때문에 판정을 버릴 이유는 없다)."""
        return min(1.0, max(0.0, value))


@lru_cache
def load_prompt_template() -> tuple[str, str]:
    """`assess.md` 를 읽어 (시스템, 사용자 템플릿) 을 돌려준다."""
    if not PROMPT_PATH.exists():
        raise PromptLoadError(f"프롬프트 파일이 없다: {PROMPT_PATH}")

    raw = PROMPT_PATH.read_text(encoding="utf-8")
    if _SYSTEM_MARKER not in raw or _USER_MARKER not in raw:
        raise PromptLoadError(
            f"프롬프트 파일에 {_SYSTEM_MARKER} / {_USER_MARKER} 구분자가 없다: {PROMPT_PATH}"
        )

    _, remainder = raw.split(_SYSTEM_MARKER, 1)
    system, user = remainder.split(_USER_MARKER, 1)
    return system.strip(), user.strip()


def _bullet_list(values: list[str], empty: str) -> str:
    """문자열 목록을 `- ` 글머리표로 만든다. 비면 `empty` 문구를 쓴다."""
    items = [str(value).strip() for value in values if str(value).strip()]
    if not items:
        return empty
    return "\n".join(f"- {item}" for item in items)


def build_evidence_section(chunks: list[ChunkRef], evidences: list[EvidenceBlock]) -> str:
    """`## 근거` 블록 본문을 만든다."""
    blocks = [chunk.to_block() for chunk in chunks]
    blocks.extend(evidence.to_block() for evidence in evidences)
    if not blocks:
        return NO_EVIDENCE_TEXT
    return "\n".join(blocks)


def build_assess_prompt(
    criterion: CriterionPrompt,
    *,
    rule_text: str,
    chunks: list[ChunkRef],
    evidences: list[EvidenceBlock],
) -> AssessPrompt:
    """항목 1개의 심사 프롬프트를 조립한다."""
    system, user_template = load_prompt_template()

    replacements = {
        "{{code}}": criterion.code,
        "{{title}}": criterion.title,
        "{{chapter}}": str(criterion.chapter),
        "{{section}}": criterion.section,
        "{{requirement}}": criterion.requirement.strip(),
        "{{checkpoints}}": _bullet_list(criterion.checkpoints, "- (안내서에 확인사항 없음)"),
        "{{defect_examples}}": _bullet_list(
            criterion.defect_examples, "- (안내서에 결함사례 없음)"
        ),
        "{{rule_results}}": rule_text,
        "{{evidence_blocks}}": build_evidence_section(chunks, evidences),
    }

    user = user_template
    for placeholder, value in replacements.items():
        user = user.replace(placeholder, value)
    return AssessPrompt(system=system, user=user)
