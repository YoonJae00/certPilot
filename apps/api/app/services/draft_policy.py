"""정보보호 정책 초안 생성 (PRD §7 F4).

뼈대는 `data/templates/policy_ko.md` 하나뿐이다. 조항 본문을 코드에 적지 않는다
(인증기준과 마찬가지로 문서 원문은 항상 파일에서 읽는다).

템플릿의 `{{placeholder}}` 는 프로젝트 설정으로 채우고, 시스템이 알 수 없는 값
(서비스명·CISO 지정 현황·시행일 등)은 `[확인 필요]` 로 남긴다.
"""

from pathlib import Path
from typing import Any

from app.models import Project
from app.services.draft_common import NEEDS_REVIEW, DraftSourceError, count_needs_review

# apps/api/app/services/draft_policy.py -> 리포 루트
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_TEMPLATE_PATH = REPO_ROOT / "data" / "templates" / "policy_ko.md"

_TITLE_PREFIX = "# "
_HEADING_PREFIX = "## "


def _placeholder_values(project: Project) -> dict[str, str]:
    """템플릿 플레이스홀더 값을 프로젝트 설정에서 만든다.

    프로젝트에 대응하는 필드가 없는 값은 지어내지 않고 `[확인 필요]` 로 남긴다.
    서비스명·CISO 필드가 프로젝트 설정에 생기면 여기서 연결하면 된다.
    """
    scope = (project.scope_text or "").strip()
    return {
        # 회사명은 프로젝트명을 쓴다(프로젝트가 곧 인증 준비 단위다).
        "company_name": project.name.strip() or NEEDS_REVIEW,
        "service_name": NEEDS_REVIEW,
        "cert_type": project.cert_type.value,
        "cert_scope": scope or NEEDS_REVIEW,
        "ciso_name": NEEDS_REVIEW,
        "ciso_assigned": NEEDS_REVIEW,
        "contact_email": NEEDS_REVIEW,
        "effective_date": NEEDS_REVIEW,
        "audit_due_date": (
            project.audit_due_date.isoformat() if project.audit_due_date else NEEDS_REVIEW
        ),
    }


def _fill(text: str, values: dict[str, str]) -> str:
    """`{{key}}` 를 값으로 바꾼다. 값을 모르는 키는 `[확인 필요]` 로 남긴다."""
    filled = text
    for key, value in values.items():
        filled = filled.replace(f"{{{{{key}}}}}", value)
    # 값 표가 비어 있는 플레이스홀더도 사람이 채우도록 표시만 남긴다.
    while "{{" in filled and "}}" in filled:
        start = filled.index("{{")
        end = filled.index("}}", start)
        filled = f"{filled[:start]}{NEEDS_REVIEW}{filled[end + 2 :]}"
    return filled


def load_policy_template(path: Path | None = None) -> tuple[str, list[tuple[str, list[str]]]]:
    """정책 템플릿을 `(제목, [(조항 제목, 본문 줄)])` 로 읽는다."""
    source = path or DEFAULT_TEMPLATE_PATH
    if not source.exists():
        raise DraftSourceError(f"정책 템플릿 파일이 없다: {source}")

    title = ""
    sections: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None

    for raw in source.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith(_HEADING_PREFIX):
            current = (line[len(_HEADING_PREFIX) :].strip(), [])
            sections.append(current)
            continue
        if line.startswith(_TITLE_PREFIX):
            title = line[len(_TITLE_PREFIX) :].strip()
            continue
        if not line.strip():
            continue
        if current is not None:
            current[1].append(line.strip())

    if not sections:
        raise DraftSourceError(f"정책 템플릿에 조항이 없다: {source}")
    return title, sections


def build_policy_content(project: Project, *, path: Path | None = None) -> dict[str, Any]:
    """정책 초안 `content_json` 을 만든다."""
    title, sections = load_policy_template(path)
    values = _placeholder_values(project)

    rendered = [
        {
            "heading": _fill(heading, values),
            "body": _fill("\n".join(lines), values),
        }
        for heading, lines in sections
    ]

    return {
        "title": _fill(title, values) or f"{values['company_name']} 정보보호 정책",
        "sections": rendered,
        "stats": {
            "total": len(rendered),
            # 사람이 채워야 할 칸(조항 제목·본문 중 `[확인 필요]` 가 남은 곳) 수.
            "needs_review": count_needs_review(rendered),
        },
    }
