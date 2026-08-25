"""모의심사 라우터 (PRD §7 F3, F7 일부).

모든 엔드포인트는 `load_scoped_project` 로 org 스코프를 먼저 확정한다. 다른 조직의
프로젝트 ID 를 넣으면 존재 여부를 흘리지 않도록 404, 심사원(reviewer)은 403 이다.
"""

import uuid
from collections.abc import Iterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, asc, desc, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import CurrentUser, load_scoped_project, require_roles
from app.models import (
    Assessment,
    AssessmentStatus,
    Chunk,
    Criterion,
    Document,
    Evidence,
    Finding,
    FindingStatus,
    User,
    UserRole,
)
from app.schemas.assessment import (
    AssessmentOut,
    FindingChunkOut,
    FindingDetailOut,
    FindingEvidenceOut,
    FindingOut,
)
from app.services.audit import record_audit
from app.services.report import build_gap_report
from app.services.scoring import code_sort_key
from app.workers.assess import enqueue_assessment, start_assessment_thread

router = APIRouter(prefix="/projects", tags=["assessments"])

AssessmentRunner = Annotated[User, Depends(require_roles(UserRole.ORG_ADMIN))]

AUDIT_START_ACTION = "assessment.start"
AUDIT_REPORT_ACTION = "assessment.report_download"

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, detail="리소스를 찾을 수 없다")

FindingSort = Literal["code", "status", "confidence", "-confidence"]


def _get_assessment(db: Session, project_id: uuid.UUID, assessment_id: uuid.UUID) -> Assessment:
    """프로젝트 스코프 안에서만 모의심사를 읽는다."""
    assessment = db.execute(
        select(Assessment).where(
            Assessment.id == assessment_id, Assessment.project_id == project_id
        )
    ).scalar_one_or_none()
    if assessment is None:
        raise _NOT_FOUND
    return assessment


@router.post(
    "/{project_id}/assessments",
    response_model=AssessmentOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_assessment(
    project_id: uuid.UUID,
    user: AssessmentRunner,
    db: Annotated[Session, Depends(get_db)],
) -> AssessmentOut:
    """모의심사를 시작한다. 실행은 비동기이므로 202 와 함께 queued 상태로 돌려준다."""
    project = load_scoped_project(db, user, project_id)

    assessment = Assessment(project_id=project.id, status=AssessmentStatus.QUEUED)
    db.add(assessment)
    db.flush()
    record_audit(
        db,
        action=AUDIT_START_ACTION,
        org_id=project.org_id,
        user_id=user.id,
        target=str(assessment.id),
        meta={"project_id": str(project.id)},
    )
    db.commit()
    db.refresh(assessment)

    # 브로커가 없으면(데모·로컬) 백그라운드 스레드로 그대로 실행한다.
    if not enqueue_assessment(assessment.id):
        start_assessment_thread(assessment.id)

    return AssessmentOut.model_validate(assessment)


@router.get("/{project_id}/assessments", response_model=list[AssessmentOut])
def list_assessments(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> list[AssessmentOut]:
    """모의심사 목록(최신순)."""
    project = load_scoped_project(db, user, project_id)
    rows = list(
        db.execute(
            select(Assessment)
            .where(Assessment.project_id == project.id)
            .order_by(desc(Assessment.created_at), desc(Assessment.id))
        ).scalars()
    )
    return [AssessmentOut.model_validate(row) for row in rows]


@router.get("/{project_id}/assessments/{assessment_id}", response_model=AssessmentOut)
def get_assessment(
    project_id: uuid.UUID,
    assessment_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> AssessmentOut:
    """모의심사 상세(진행률·집계 포함)."""
    project = load_scoped_project(db, user, project_id)
    return AssessmentOut.model_validate(_get_assessment(db, project.id, assessment_id))


def _apply_finding_filters(
    statement: Select[tuple[Finding, Criterion]],
    *,
    finding_status: FindingStatus | None,
    chapter: int | None,
    q: str | None,
) -> Select[tuple[Finding, Criterion]]:
    """판정 목록 필터를 건다."""
    if finding_status is not None:
        statement = statement.where(Finding.status == finding_status)
    if chapter is not None:
        statement = statement.where(Criterion.chapter == chapter)
    if q:
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            or_(
                Finding.criterion_code.ilike(pattern),
                Criterion.title.ilike(pattern),
                Criterion.section.ilike(pattern),
                Finding.rationale.ilike(pattern),
            )
        )
    return statement


@router.get(
    "/{project_id}/assessments/{assessment_id}/findings",
    response_model=list[FindingOut],
)
def list_findings(
    project_id: uuid.UUID,
    assessment_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    finding_status: Annotated[
        FindingStatus | None, Query(alias="status", description="판정 필터")
    ] = None,
    chapter: Annotated[int | None, Query(ge=1, le=3, description="장 필터")] = None,
    q: Annotated[str | None, Query(description="항목 코드·제목·근거 검색")] = None,
    sort: Annotated[FindingSort, Query(description="정렬 기준")] = "code",
) -> list[FindingOut]:
    """항목별 판정 목록. 인증기준 제목·분류를 조인해 준다."""
    project = load_scoped_project(db, user, project_id)
    assessment = _get_assessment(db, project.id, assessment_id)

    statement = (
        select(Finding, Criterion)
        .join(Criterion, Criterion.code == Finding.criterion_code)
        .where(Finding.assessment_id == assessment.id)
    )
    statement = _apply_finding_filters(
        statement, finding_status=finding_status, chapter=chapter, q=q
    )

    if sort == "status":
        statement = statement.order_by(asc(Finding.status), asc(Finding.criterion_code))
    elif sort == "confidence":
        statement = statement.order_by(asc(Finding.confidence), asc(Finding.criterion_code))
    elif sort == "-confidence":
        statement = statement.order_by(desc(Finding.confidence), asc(Finding.criterion_code))
    else:
        statement = statement.order_by(asc(Finding.criterion_code))

    rows = db.execute(statement).all()
    results = [_to_finding_out(finding, criterion) for finding, criterion in rows]
    if sort == "code":
        # 문자열 정렬이면 2.10.1 이 2.2.1 앞으로 온다. 사람이 보는 순서로 다시 맞춘다.
        results.sort(key=lambda row: code_sort_key(row.criterion_code))
    return results


def _to_finding_out(finding: Finding, criterion: Criterion) -> FindingOut:
    """판정 + 인증기준을 응답 모델로 옮긴다."""
    return FindingOut(
        id=finding.id,
        criterion_code=finding.criterion_code,
        chapter=criterion.chapter,
        section=criterion.section,
        title=criterion.title,
        status=finding.status,
        confidence=round(float(finding.confidence), 4),
        rationale=finding.rationale,
        evidence_chunk_ids=[str(item) for item in (finding.evidence_chunk_ids or [])],
        evidence_ids=[str(item) for item in (finding.evidence_ids or [])],
        predicted_defect=finding.predicted_defect,
        recommendation=finding.recommendation,
        decided_by=finding.decided_by,
        created_at=finding.created_at,
    )


def _to_uuids(values: list[object]) -> list[uuid.UUID]:
    """저장된 참조 문자열을 UUID 로 바꾼다. 형식이 아니면 버린다."""
    parsed: list[uuid.UUID] = []
    for value in values or []:
        try:
            parsed.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return parsed


@router.get(
    "/{project_id}/assessments/{assessment_id}/findings/{finding_id}",
    response_model=FindingDetailOut,
)
def get_finding(
    project_id: uuid.UUID,
    assessment_id: uuid.UUID,
    finding_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> FindingDetailOut:
    """판정 상세. 근거 청크 본문과 증적 payload 를 함께 준다(근거 하이라이트용)."""
    project = load_scoped_project(db, user, project_id)
    assessment = _get_assessment(db, project.id, assessment_id)

    row = db.execute(
        select(Finding, Criterion)
        .join(Criterion, Criterion.code == Finding.criterion_code)
        .where(Finding.id == finding_id, Finding.assessment_id == assessment.id)
    ).one_or_none()
    if row is None:
        raise _NOT_FOUND
    finding, criterion = row

    chunk_ids = _to_uuids(list(finding.evidence_chunk_ids or []))
    chunks: list[FindingChunkOut] = []
    if chunk_ids:
        chunk_rows = db.execute(
            select(Chunk.id, Chunk.document_id, Chunk.page, Chunk.text, Document.filename)
            .join(Document, Document.id == Chunk.document_id)
            # 다른 조직 문서의 청크는 절대 실리지 않는다.
            .where(Chunk.id.in_(chunk_ids), Document.project_id == project.id)
        ).all()
        by_id = {
            item.id: FindingChunkOut(
                chunk_id=item.id,
                document_id=item.document_id,
                filename=item.filename,
                page=item.page,
                text=item.text,
            )
            for item in chunk_rows
        }
        chunks = [by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in by_id]

    evidence_ids = _to_uuids(list(finding.evidence_ids or []))
    evidence: list[FindingEvidenceOut] = []
    if evidence_ids:
        evidence_rows = list(
            db.execute(
                select(Evidence).where(
                    Evidence.id.in_(evidence_ids), Evidence.project_id == project.id
                )
            ).scalars()
        )
        by_evidence = {
            item.id: FindingEvidenceOut(
                evidence_id=item.id,
                source=item.source,
                check_id=item.check_id,
                status=item.status,
                collected_at=item.collected_at,
                payload_json=dict(item.payload_json or {}),
            )
            for item in evidence_rows
        }
        evidence = [
            by_evidence[evidence_id] for evidence_id in evidence_ids if evidence_id in by_evidence
        ]

    base = _to_finding_out(finding, criterion)
    return FindingDetailOut(
        **base.model_dump(),
        criterion_requirement=criterion.requirement,
        chunks=chunks,
        evidence=evidence,
    )


@router.get("/{project_id}/assessments/{assessment_id}/report.xlsx")
def download_gap_report(
    project_id: uuid.UUID,
    assessment_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    """갭 리포트 XLSX 를 내려준다(시트 3개)."""
    project = load_scoped_project(db, user, project_id)
    assessment = _get_assessment(db, project.id, assessment_id)

    payload = build_gap_report(db, project, assessment)
    record_audit(
        db,
        action=AUDIT_REPORT_ACTION,
        org_id=project.org_id,
        user_id=user.id,
        target=str(assessment.id),
        meta={"bytes": len(payload)},
    )
    db.commit()

    filename = f"certpilot-gap-report-{assessment.id}.xlsx"

    def stream() -> Iterator[bytes]:
        yield payload

    return StreamingResponse(
        stream(),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
