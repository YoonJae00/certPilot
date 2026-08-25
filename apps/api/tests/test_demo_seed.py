"""데모 시드 테스트 (부록 B Task 11).

`make demo` 가 만드는 상태를 그대로 검증한다. PRD §4 3분 시나리오를 따라가려면
아래가 모두 있어야 한다: 조직·계정 4개·프로젝트·인증기준 101행·문서 12개(전부 파싱)·
증적 스냅샷 2회·변경 감지 알림·완료된 모의심사·검수 대기 초안.

여기 쓰는 비밀번호·자격증명은 전부 시드가 정한 가짜 값이다.
"""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connectors.credentials import SECRET_FIELD, load_auth
from app.core.security import verify_password
from app.models import (
    Alert,
    AlertType,
    Assessment,
    AssessmentStatus,
    Chunk,
    Connector,
    ConnectorStatus,
    ConnectorType,
    Criterion,
    DecidedBy,
    Document,
    DocumentStatus,
    Draft,
    DraftKind,
    DraftStatus,
    Evidence,
    EvidenceStatus,
    Finding,
    FindingStatus,
    Organization,
    OrgPlan,
    Project,
    ReviewTask,
    ReviewTaskStatus,
    User,
    UserRole,
)
from app.services.demo_seed import (
    ADMIN_EMAIL,
    AUDIT_DUE_IN_DAYS,
    DEMO_ACCOUNTS,
    DEMO_ORG_NAME,
    DEMO_PASSWORD,
    DEMO_PROJECT_NAME,
    DEMO_SECRET_ACCESS_KEY,
    DRIFTED_CHECK_ID,
    SHOWCASE_CRITERION_CODE,
    DemoSeedResult,
    drift_alert_message,
    pending_review_task_count,
    seed_demo,
    showcase_finding,
)
from app.services.draft_docx import load_draft_docx

# 부록 D 샘플 문서 수와 §9 AWS 점검 수.
EXPECTED_DOCUMENTS = 12
EXPECTED_CRITERIA = 101
EXPECTED_CHECKS = 10
EXPECTED_SNAPSHOTS = 2
EXPECTED_EVIDENCE = EXPECTED_CHECKS * EXPECTED_SNAPSHOTS


@pytest.fixture
def seeded(db: Session, storage) -> DemoSeedResult:
    """시드를 한 번 실행한다. `storage` 는 moto 가짜 S3 다."""
    return seed_demo(db)


def _count(db: Session, model) -> int:
    """모델 전체 행 수."""
    return int(db.execute(select(func.count()).select_from(model)).scalar_one())


def _state(db: Session) -> dict[str, object]:
    """멱등성 비교용 상태 요약. 매번 새로 만들어지는 id 는 넣지 않는다."""
    project = db.execute(select(Project).where(Project.name == DEMO_PROJECT_NAME)).scalar_one()
    assessment = db.execute(select(Assessment)).scalars().one()
    draft = db.execute(select(Draft)).scalars().one()
    return {
        "organizations": _count(db, Organization),
        "users": _count(db, User),
        "user_emails": sorted(db.execute(select(User.email)).scalars()),
        "projects": _count(db, Project),
        "project_due": project.audit_due_date,
        "criteria": _count(db, Criterion),
        "documents": _count(db, Document),
        "chunks": _count(db, Chunk),
        "connectors": _count(db, Connector),
        "evidence": _count(db, Evidence),
        "snapshots": len(set(db.execute(select(Evidence.snapshot_id)).scalars())),
        "alerts": _count(db, Alert),
        "alert_messages": sorted(db.execute(select(Alert.message)).scalars()),
        "assessment_status": assessment.status,
        "findings": _count(db, Finding),
        "draft_status": draft.status,
        "review_tasks": _count(db, ReviewTask),
    }


def test_seed_creates_org_users_project_and_documents(
    db: Session, seeded: DemoSeedResult
) -> None:
    """조직 1개·계정 4개·프로젝트 1개·문서 12개(전부 파싱)·인증기준 101행."""
    org = db.execute(
        select(Organization).where(Organization.name == DEMO_ORG_NAME)
    ).scalar_one()
    assert org.plan is OrgPlan.SIMPLIFIED
    assert org.id == seeded.org_id

    users = list(db.execute(select(User).order_by(User.email)).scalars())
    assert len(users) == len(DEMO_ACCOUNTS) == 4
    by_email = {user.email: user for user in users}
    for email, role, in_org in DEMO_ACCOUNTS:
        user = by_email[email]
        assert user.role is role
        # reviewer·operator 는 조직에 속하지 않는다.
        assert (user.org_id == org.id) is in_org
        assert verify_password(DEMO_PASSWORD, user.password_hash)
    assert by_email[ADMIN_EMAIL].role is UserRole.ORG_ADMIN

    project = db.execute(select(Project)).scalars().one()
    assert project.id == seeded.project_id
    assert project.name == DEMO_PROJECT_NAME
    assert project.is_simplified is True
    assert project.scope_text
    assert project.audit_due_date == date.today() + timedelta(days=AUDIT_DUE_IN_DAYS)

    assert _count(db, Criterion) == EXPECTED_CRITERIA == seeded.criteria_count

    documents = list(db.execute(select(Document)).scalars())
    assert len(documents) == EXPECTED_DOCUMENTS == seeded.document_count
    assert all(document.status is DocumentStatus.PARSED for document in documents)
    assert all(document.project_id == project.id for document in documents)
    assert _count(db, Chunk) > 0


def test_seed_creates_two_snapshots_and_drift_alert(
    db: Session, seeded: DemoSeedResult
) -> None:
    """스냅샷 2회 = 증적 20행, 변경 감지 알림 1건(미읽음), 자격증명은 암호문뿐."""
    connector = db.execute(select(Connector)).scalars().one()
    assert connector.type is ConnectorType.AWS
    assert connector.status is ConnectorStatus.CONNECTED
    assert connector.last_collected_at is not None

    # 평문 자격증명이 어디에도 남지 않는다(PRD §10).
    config = dict(connector.config_json)
    assert DEMO_SECRET_ACCESS_KEY not in str(config)
    assert config[SECRET_FIELD]
    # 그래도 복호화하면 커넥터가 쓸 수 있는 형태로 나온다.
    assert load_auth(config).region == config["region"]

    evidence = list(db.execute(select(Evidence)).scalars())
    assert len(evidence) == EXPECTED_EVIDENCE == seeded.evidence_count
    snapshots = {row.snapshot_id for row in evidence}
    assert len(snapshots) == EXPECTED_SNAPSHOTS
    assert all(row.connector_id == connector.id for row in evidence)
    assert all(row.criterion_codes for row in evidence)

    latest = max(row.collected_at for row in evidence)
    current = [row for row in evidence if row.collected_at == latest]
    assert len(current) == EXPECTED_CHECKS
    drifted = next(row for row in current if row.check_id == DRIFTED_CHECK_ID)
    assert drifted.status is EvidenceStatus.FAIL
    assert drifted.payload_json["unblocked"]

    alerts = list(db.execute(select(Alert).where(Alert.type == AlertType.DRIFT)).scalars())
    assert len(alerts) >= 1
    assert len(alerts) == seeded.alert_count
    # 데모에서 "새 알림" 으로 보여야 하므로 미읽음이다.
    assert all(alert.read_at is None for alert in alerts)
    message = drift_alert_message(db, seeded.project_id)
    assert message is not None
    assert "S3 퍼블릭 액세스 차단" in message


def test_seed_runs_assessment_backed_by_evidence(db: Session, seeded: DemoSeedResult) -> None:
    """모의심사가 done 으로 끝나고 101개 판정이 남는다. MFA fail 은 규칙이 unmet 으로 덮는다."""
    assessment = db.execute(select(Assessment)).scalars().one()
    assert assessment.id == seeded.assessment_id
    assert assessment.status is AssessmentStatus.DONE
    assert assessment.finished_at is not None

    findings = list(db.execute(select(Finding)).scalars())
    assert len(findings) == EXPECTED_CRITERIA == seeded.finding_count
    assert len({finding.criterion_code for finding in findings}) == EXPECTED_CRITERIA

    showcase = showcase_finding(db, assessment.id)
    assert showcase is not None, f"{SHOWCASE_CRITERION_CODE} 판정이 없다"
    # PRD §4 1:40 장면: IAM 사용자 MFA fail 증적 때문에 규칙이 미충족으로 판정한다.
    assert showcase.status is FindingStatus.UNMET
    assert showcase.decided_by is DecidedBy.RULE
    assert showcase.evidence_ids


def test_seed_leaves_sow_draft_in_review_queue(db: Session, seeded: DemoSeedResult) -> None:
    """운영명세서 초안은 검수 대기, 검수 과제는 미배정 상태로 큐에 올라 있다."""
    draft = db.execute(select(Draft)).scalars().one()
    assert draft.id == seeded.draft_id
    assert draft.kind is DraftKind.SOW
    assert draft.status is DraftStatus.IN_REVIEW
    assert draft.content_json.get("rows")
    assert draft.docx_s3_key
    # 승인 전이라 화면에서는 못 받지만 파일 자체는 만들어져 있다.
    assert load_draft_docx(draft.docx_s3_key)[:2] == b"PK"

    task = db.execute(select(ReviewTask)).scalars().one()
    assert task.draft_id == draft.id
    assert task.status is ReviewTaskStatus.PENDING
    # 심사원이 열어 볼 때 배정된다(미배정으로 공용 큐에 있어야 한다).
    assert task.reviewer_id is None
    assert pending_review_task_count(db, seeded.project_id) == 1


def test_seed_is_idempotent(db: Session, storage) -> None:
    """두 번 실행해도 같은 상태다. 남은 조직·계정·문서가 두 배가 되지 않는다."""
    first = seed_demo(db)
    before = _state(db)

    second = seed_demo(db)
    after = _state(db)

    assert before == after
    # 데이터는 매번 새로 만들어지므로 식별자는 달라진다.
    assert first.project_id != second.project_id
    assert before["organizations"] == 1
    assert before["users"] == 4
    assert before["documents"] == EXPECTED_DOCUMENTS
    assert before["evidence"] == EXPECTED_EVIDENCE
    assert before["findings"] == EXPECTED_CRITERIA


def test_seed_purges_previous_demo_rows(db: Session, storage) -> None:
    """재실행은 이전 데모 데이터를 지운다. 다른 조직 데이터는 건드리지 않는다."""
    from tests.conftest import make_org, make_project, make_user

    other_org = make_org(db, "다른조직")
    other_user = make_user(
        db, email="keep-me@example.com", role=UserRole.ORG_ADMIN, org_id=other_org.id
    )
    other_project = make_project(db, other_org.id, "다른프로젝트")

    first = seed_demo(db)
    old_project_id: uuid.UUID = first.project_id
    seed_demo(db)

    assert db.get(Project, old_project_id) is None
    assert (
        db.execute(
            select(func.count()).select_from(Document).where(Document.project_id == old_project_id)
        ).scalar_one()
        == 0
    )
    # 남의 조직은 그대로다.
    assert db.get(Organization, other_org.id) is not None
    assert db.get(User, other_user.id) is not None
    assert db.get(Project, other_project.id) is not None
