"""증적 패키지 ZIP 생성 (PRD §7 F7).

구조:

```
README.md                       프로젝트·생성 시각·마스킹 상태·폴더 설명
{criterion_code}/finding.json   판정 요약(status·confidence·rationale·decided_by)
{criterion_code}/evidence_*.json  해당 항목에 매핑된 클라우드 증적 payload
{criterion_code}/document_sources.md  근거 청크 출처(문서명·페이지·발췌)
```

근거(문서 청크·클라우드 증적)가 하나도 없는 항목은 폴더를 만들지 않고 README 에
목록으로만 남긴다. 빈 폴더가 "근거가 있는데 못 담았다"로 오해되지 않게 하기 위해서다.

`evidence.payload_json` 에는 수집 단계에서 이미 자격증명이 들어가지 않지만, 패키지는
조직 밖으로 나가는 산출물이므로 계정 ID·ARN·액세스 키·비밀 값을 여기서 한 번 더
마스킹한다(방어적 재확인, PRD §10).
"""

import io
import json
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.aws import mask_access_key_id, mask_account_id, mask_arn
from app.connectors.mapping import MappingError, get_mapping
from app.models import (
    Assessment,
    Chunk,
    Criterion,
    Document,
    Evidence,
    Finding,
    Project,
)
from app.services.masking import mask_text
from app.services.report import DECIDED_BY_LABELS, STATUS_LABELS
from app.services.scoring import code_sort_key

# 문서 출처에 싣는 발췌 길이(자).
EXCERPT_LENGTH = 200

README_NAME = "README.md"
FINDING_NAME = "finding.json"
DOCUMENT_SOURCES_NAME = "document_sources.md"

# 마스킹 치환 문자열. 어떤 종류를 지웠는지 사람이 알 수 있게 타입을 남긴다.
MASKED_SECRET = "[MASKED:secret]"

# 키 이름으로 마스킹 종류를 고른다. 과하게 지우는 쪽이 안전하다.
_SECRET_KEY_HINTS = ("secret", "token", "password", "passwd", "credential", "private_key")
_ACCESS_KEY_HINTS = ("access_key", "accesskey", "key_id", "keyid")
_ACCOUNT_KEY_HINTS = ("account", "owner")

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S %Z"


def _format_time(value: datetime | None) -> str:
    """일시를 한국어 문서에 넣을 문자열로 바꾼다. 값이 없으면 '기록 없음'."""
    if value is None:
        return "기록 없음"
    return value.strftime(_TIME_FORMAT).strip()


def _mask_scalar(key: str, value: Any) -> Any:
    """키 이름을 보고 문자열 값을 마스킹한다. 문자열이 아니면 그대로 둔다."""
    if not isinstance(value, str):
        return value

    lowered = key.lower()
    if any(hint in lowered for hint in _SECRET_KEY_HINTS):
        return MASKED_SECRET
    if "arn" in lowered or value.startswith("arn:"):
        return mask_arn(value)
    if any(hint in lowered for hint in _ACCESS_KEY_HINTS):
        return mask_access_key_id(value)
    if any(hint in lowered for hint in _ACCOUNT_KEY_HINTS):
        return mask_account_id(value)
    # 남은 문자열에는 개인정보 패턴 마스킹을 한 번 더 건다.
    return mask_text(value)


def mask_payload(payload: Any, *, key: str = "") -> Any:
    """증적 payload 를 재귀적으로 훑어 식별자·비밀 값을 마스킹한다."""
    if isinstance(payload, dict):
        return {str(name): mask_payload(item, key=str(name)) for name, item in payload.items()}
    if isinstance(payload, list):
        return [mask_payload(item, key=key) for item in payload]
    return _mask_scalar(key, payload)


@dataclass(frozen=True)
class ChunkSource:
    """근거 청크 1건의 출처."""

    filename: str
    page: int | None
    excerpt: str


@dataclass
class PackageItem:
    """패키지에 들어가는 항목 1개."""

    code: str
    criterion: Criterion
    finding: Finding
    chunks: list[ChunkSource] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        """근거가 하나라도 있는지."""
        return bool(self.chunks or self.evidence)


def _to_uuids(values: list[Any]) -> list[uuid.UUID]:
    """저장된 참조 문자열을 UUID 로 바꾼다. 형식이 아니면 버린다."""
    parsed: list[uuid.UUID] = []
    for value in values or []:
        try:
            parsed.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return parsed


def _excerpt(text: str) -> str:
    """청크 본문 앞 200자를 한 줄로 만든다.

    청크는 적재 단계에서 이미 마스킹돼 있지만, 패키지가 조직 밖으로 나가는 산출물이라
    개인정보 패턴을 한 번 더 지운다.
    """
    flattened = " ".join(mask_text(text or "").split())
    if len(flattened) <= EXCERPT_LENGTH:
        return flattened
    return f"{flattened[:EXCERPT_LENGTH]}…"


def _load_chunk_sources(
    db: Session, project_id: uuid.UUID, findings: list[Finding]
) -> dict[uuid.UUID, ChunkSource]:
    """판정들이 참조한 청크 출처를 한 번에 읽는다(프로젝트 스코프)."""
    chunk_ids: list[uuid.UUID] = []
    for finding in findings:
        chunk_ids.extend(_to_uuids(list(finding.evidence_chunk_ids or [])))
    if not chunk_ids:
        return {}

    rows = db.execute(
        select(Chunk.id, Chunk.page, Chunk.text, Document.filename)
        .join(Document, Document.id == Chunk.document_id)
        # 다른 조직 문서의 청크는 절대 실리지 않는다.
        .where(Chunk.id.in_(set(chunk_ids)), Document.project_id == project_id)
    ).all()
    return {
        row.id: ChunkSource(filename=row.filename, page=row.page, excerpt=_excerpt(row.text))
        for row in rows
    }


def _latest_by_check(rows: list[Evidence]) -> list[Evidence]:
    """같은 점검이 여러 스냅샷에 있으면 최신 것만 남긴다."""
    newest: dict[str, Evidence] = {}
    for row in rows:
        current = newest.get(row.check_id)
        if current is None or row.collected_at > current.collected_at:
            newest[row.check_id] = row
    return list(newest.values())


def _evidence_for_item(
    code: str, finding: Finding, evidence_rows: list[Evidence]
) -> list[Evidence]:
    """항목에 붙일 증적을 고른다.

    판정이 실제로 인용한 증적(`evidence_ids`)을 먼저 담고, 매핑(`criterion_codes`)으로
    연결된 점검 중 최신 스냅샷을 더한다. 같은 증적이 두 번 들어가지 않게 id 로 거른다.
    """
    referenced_ids = set(_to_uuids(list(finding.evidence_ids or [])))
    selected: dict[uuid.UUID, Evidence] = {
        row.id: row for row in evidence_rows if row.id in referenced_ids
    }

    mapped = [row for row in evidence_rows if code in (row.criterion_codes or [])]
    for row in _latest_by_check(mapped):
        selected.setdefault(row.id, row)

    return sorted(selected.values(), key=lambda row: (row.check_id, row.collected_at))


def _build_items(db: Session, project: Project, assessment: Assessment) -> list[PackageItem]:
    """판정·인증기준·근거를 모아 패키지 항목 목록을 만든다."""
    records = db.execute(
        select(Finding, Criterion)
        .join(Criterion, Criterion.code == Finding.criterion_code)
        .where(Finding.assessment_id == assessment.id)
    ).all()
    findings = [finding for finding, _ in records]

    chunk_sources = _load_chunk_sources(db, project.id, findings)
    evidence_rows = list(
        db.execute(select(Evidence).where(Evidence.project_id == project.id)).scalars()
    )

    items: list[PackageItem] = []
    for finding, criterion in records:
        chunks = [
            chunk_sources[chunk_id]
            for chunk_id in _to_uuids(list(finding.evidence_chunk_ids or []))
            if chunk_id in chunk_sources
        ]
        items.append(
            PackageItem(
                code=finding.criterion_code,
                criterion=criterion,
                finding=finding,
                chunks=chunks,
                evidence=_evidence_for_item(finding.criterion_code, finding, evidence_rows),
            )
        )

    items.sort(key=lambda item: code_sort_key(item.code))
    return items


def _check_title(check_id: str) -> str | None:
    """점검 표시명. 매핑 파일이 없거나 모르는 점검이면 None."""
    try:
        mapping = get_mapping(check_id)
    except MappingError:
        return None
    return mapping.title if mapping else None


def _finding_document(item: PackageItem) -> str:
    """`finding.json` 본문."""
    finding = item.finding
    payload: dict[str, Any] = {
        "criterion_code": item.code,
        "title": item.criterion.title,
        "chapter": item.criterion.chapter,
        "section": item.criterion.section,
        "status": finding.status.value,
        "status_label": STATUS_LABELS[finding.status],
        "confidence": round(float(finding.confidence), 4),
        "rationale": mask_text(finding.rationale or ""),
        "decided_by": finding.decided_by.value,
        "decided_by_label": DECIDED_BY_LABELS.get(
            finding.decided_by.value, finding.decided_by.value
        ),
        "predicted_defect": mask_text(finding.predicted_defect or "") or None,
        "recommendation": mask_text(finding.recommendation or "") or None,
        "document_source_count": len(item.chunks),
        "evidence_count": len(item.evidence),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _evidence_document(evidence: Evidence) -> str:
    """`evidence_*.json` 본문. payload 는 마스킹해서 담는다."""
    payload: dict[str, Any] = {
        "evidence_id": str(evidence.id),
        "source": evidence.source,
        "check_id": evidence.check_id,
        "check_title": _check_title(evidence.check_id),
        "criterion_codes": list(evidence.criterion_codes or []),
        "status": evidence.status.value,
        "collected_at": evidence.collected_at.isoformat(),
        "snapshot_id": evidence.snapshot_id,
        "masked": True,
        "payload": mask_payload(dict(evidence.payload_json or {})),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _document_sources_document(item: PackageItem) -> str:
    """`document_sources.md` 본문(문서명·페이지·발췌 200자)."""
    lines = [
        f"# 근거 문서 출처 — {item.code} {item.criterion.title}",
        "",
        f"판정 근거로 인용된 문서 청크 {len(item.chunks)}건이다. "
        "발췌는 앞 200자까지이며, 개인정보는 적재 단계에서 이미 마스킹돼 있다.",
        "",
    ]
    for index, chunk in enumerate(item.chunks, start=1):
        page = f"{chunk.page}쪽" if chunk.page is not None else "쪽 정보 없음"
        lines.append(f"## {index}. {chunk.filename} ({page})")
        lines.append("")
        lines.append(f"> {chunk.excerpt}" if chunk.excerpt else "> (본문 없음)")
        lines.append("")
    return "\n".join(lines)


def _readme_document(
    project: Project,
    assessment: Assessment,
    items: list[PackageItem],
    *,
    generated_at: datetime,
    last_collected_at: datetime | None,
) -> str:
    """루트 `README.md` 본문."""
    included = [item for item in items if item.has_evidence]
    skipped = [item for item in items if not item.has_evidence]

    lines = [
        "# 증적 패키지",
        "",
        f"- 프로젝트: {project.name} ({project.cert_type.value})",
        f"- 모의심사 ID: {assessment.id}",
        f"- 모의심사 완료 일시: {_format_time(assessment.finished_at)}",
        f"- 증적 최신 수집 시각: {_format_time(last_collected_at)}",
        f"- 패키지 생성 시각: {_format_time(generated_at)}",
        f"- 폴더로 담긴 항목: {len(included)}개 / 근거가 없어 생략한 항목: {len(skipped)}개",
        "",
        "## 식별자 마스킹",
        "",
        "이 패키지에는 클라우드 자격증명(액세스 키·시크릿·세션 토큰)이 들어 있지 않다.",
        "증적 payload 는 수집 단계에서 이미 자격증명을 담지 않지만, 패키지를 만들 때",
        "다음 값을 한 번 더 마스킹한다.",
        "",
        "- 계정 ID: 뒤 4자리만 남긴다(예: `********9012`).",
        "- ARN: 계정 ID 부분만 가린다. 역할·리소스 이름은 식별에 필요해 남긴다.",
        "- 액세스 키 ID: 앞 4자·뒤 4자만 남긴다.",
        "- 비밀번호·토큰·시크릿으로 보이는 키: `[MASKED:secret]` 로 치환한다.",
        "- 문서 발췌·판정 근거: 주민등록번호·연락처·이메일·카드번호를 `[MASKED:type]` 로 치환한다.",
        "",
        "## 폴더 구조",
        "",
        "```",
        "README.md                      이 파일",
        "{항목코드}/finding.json          판정 요약(판정·확신도·근거 설명·판정 주체)",
        "{항목코드}/evidence_*.json       항목에 매핑된 클라우드 증적(마스킹 완료)",
        "{항목코드}/document_sources.md   근거 문서 출처(문서명·페이지·발췌 200자)",
        "```",
        "",
        f"## 근거가 없어 폴더를 만들지 않은 항목 ({len(skipped)}건)",
        "",
    ]

    if skipped:
        lines.append("판단할 근거가 없어 `판단불가`로 남은 항목이다. 문서를 보강하거나")
        lines.append("커넥터 증적을 수집한 뒤 모의심사를 다시 실행하면 채워진다.")
        lines.append("")
        for item in skipped:
            label = STATUS_LABELS[item.finding.status]
            lines.append(f"- {item.code} {item.criterion.title} — {label}")
    else:
        lines.append("없다. 모든 항목에 근거가 하나 이상 붙어 있다.")
    lines.append("")

    return "\n".join(lines)


def build_evidence_package(db: Session, project: Project, assessment: Assessment) -> bytes:
    """증적 패키지 ZIP 을 만들어 바이트로 돌려준다."""
    items = _build_items(db, project, assessment)
    generated_at = datetime.now(UTC)
    last_collected_at = max(
        (row.collected_at for item in items for row in item.evidence), default=None
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            README_NAME,
            _readme_document(
                project,
                assessment,
                items,
                generated_at=generated_at,
                last_collected_at=last_collected_at,
            ),
        )

        for item in items:
            if not item.has_evidence:
                continue
            folder = item.code
            archive.writestr(f"{folder}/{FINDING_NAME}", _finding_document(item))
            if item.chunks:
                archive.writestr(
                    f"{folder}/{DOCUMENT_SOURCES_NAME}", _document_sources_document(item)
                )
            for index, evidence in enumerate(item.evidence, start=1):
                safe_check_id = evidence.check_id.replace("/", "_").replace(" ", "_")
                archive.writestr(
                    f"{folder}/evidence_{index:02d}_{safe_check_id}.json",
                    _evidence_document(evidence),
                )

    return buffer.getvalue()
