"""데모 시드 (PRD §4 3분 시나리오 · 부록 D 데모 시드 데이터).

가상 회사 **데모핀테크** 한 곳을 통째로 만들어, 클린 DB 에서도 §4 시나리오를 그대로
따라갈 수 있게 한다. 새 로직을 만들지 않고 이미 있는 서비스만 순서대로 호출한다:

    조직·사용자·프로젝트 → 인증기준 101행 → 문서 12개 업로드+인제스트
    → AWS 커넥터·스냅샷 2회(변경 감지 1건) → 모의심사 1회 → 운영명세서 초안·검수 과제

멱등이다. 같은 이름의 조직이 있으면 관련 데이터를 전부 지우고 다시 만든다.

여기 들어가는 회사·사람·자격증명은 전부 지어낸 더미 값이다. 실제 개인정보나 실제
클라우드 자격증명은 한 글자도 넣지 않는다(CLAUDE.md 절대 규칙 3).
"""

import copy
import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.connectors.aws import AUTH_TYPE_ACCESS_KEY, AwsAuth
from app.connectors.credentials import build_stored_config
from app.connectors.drift import DriftChange, SnapshotItem, detect_drift
from app.connectors.mapping import CheckMapping, load_check_mappings
from app.core.security import hash_password
from app.models import (
    Alert,
    AlertType,
    Assessment,
    AssessmentStatus,
    AuditLog,
    CertType,
    Chunk,
    Connector,
    ConnectorStatus,
    ConnectorType,
    Document,
    DocumentStatus,
    Draft,
    DraftKind,
    DraftStatus,
    Evidence,
    EvidenceStatus,
    Finding,
    Organization,
    OrgPlan,
    Project,
    ReviewTask,
    ReviewTaskStatus,
    User,
    UserRole,
)
from app.services.criteria_loader import count_criteria, seed_criteria
from app.services.draft_docx import draft_docx_key, render_draft_docx, store_draft_docx
from app.services.draft_sow import build_sow_content
from app.services.extraction import SUPPORTED_EXTENSIONS, extension_of
from app.services.review import ensure_review_task
from app.services.storage import StorageError, get_storage
from app.workers.assess import run_assessment
from app.workers.ingest import run_ingest

logger = logging.getLogger(__name__)

# apps/api/app/services/demo_seed.py -> 리포 루트
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SAMPLES_DIR = REPO_ROOT / "data" / "samples"

# --------------------------------------------------------------------------
# 데모 상수 (전부 가상 값이다)
# --------------------------------------------------------------------------

DEMO_ORG_NAME = "데모핀테크"
DEMO_PROJECT_NAME = "데모핀테크 ISMS-P 간편인증"

# 데모 계정 공용 비밀번호. 발표용 더미이며 운영에 쓰지 않는다.
DEMO_PASSWORD = "demo1234!"  # noqa: S105 - 데모 전용 더미 비밀번호

# (이메일, 역할, 조직 소속 여부). reviewer·operator 는 조직에 속하지 않는다.
DEMO_ACCOUNTS: tuple[tuple[str, UserRole, bool], ...] = (
    ("admin@demofintech.kr", UserRole.ORG_ADMIN, True),
    ("member@demofintech.kr", UserRole.ORG_MEMBER, True),
    ("reviewer@certpilot.kr", UserRole.REVIEWER, False),
    ("operator@certpilot.kr", UserRole.OPERATOR, False),
)

ADMIN_EMAIL = DEMO_ACCOUNTS[0][0]

# PRD §4 마지막 장면의 "사후심사 D-312".
AUDIT_DUE_IN_DAYS = 312

DEMO_SCOPE_TEXT = (
    "데모핀테크가 운영하는 간편송금 서비스(웹·모바일 앱)와 이를 뒷받침하는 "
    "AWS 서울 리전 단일 계정의 인프라, 그리고 서비스 운영·고객지원·개발 조직 "
    "25명을 인증 범위로 한다. 개인정보는 회원 가입 정보와 송금 거래 내역을 "
    "취급하며, 결제대행·고객상담 업무 일부를 외부에 위탁한다."
)

# 커넥터 자격증명. **실제 키가 아니다** — 형식만 맞춘 더미 문자열이고,
# 저장 시 Fernet 으로 암호화된다(PRD §10).
DEMO_AWS_REGION = "ap-northeast-2"
DEMO_ACCESS_KEY_ID = "AKIAEXAMPLEDEMOSEED0"  # noqa: S105 - 형식만 맞춘 더미
DEMO_SECRET_ACCESS_KEY = "demo-seed-dummy-secret-not-a-real-credential"  # noqa: S105
DEMO_ACCOUNT_ID_MASKED = "********0000"

# 1차 스냅샷 시각(어제)과 2차 스냅샷 시각(현재)의 간격.
FIRST_SNAPSHOT_AGO = timedelta(days=1)

# 부록 D: MFA 미설정 사용자 4명, 90일 초과 키 2개, 퍼블릭 차단 해제 버킷 1개.
_MFA_MISSING_USERS = ["demo-analyst-01", "demo-dev-01", "demo-dev-02", "demo-ops-01"]
_UNBLOCKED_BUCKET = "demo-fintech-public-assets"

# 1차 스냅샷의 점검 10개. (check_id, 판정, payload) — payload 키는
# `app/connectors/aws.py` 의 점검 함수가 만드는 모양과 같다.
BASE_SNAPSHOT: tuple[tuple[str, EvidenceStatus, dict[str, Any]], ...] = (
    (
        "aws.iam.root_mfa",
        EvidenceStatus.PASS,
        {"account_mfa_enabled": 1},
    ),
    (
        "aws.iam.user_mfa",
        EvidenceStatus.FAIL,
        {
            "users": 7,
            "console_users": 7,
            "mfa_enabled": 3,
            "missing": list(_MFA_MISSING_USERS),
        },
    ),
    (
        "aws.iam.password_policy",
        EvidenceStatus.PASS,
        {
            "policy_exists": True,
            "minimum_password_length": 10,
            "complexity_required": True,
            "max_password_age": 90,
            "reasons": [],
        },
    ),
    (
        "aws.iam.key_age",
        EvidenceStatus.FAIL,
        {
            "active_keys": 9,
            "expired_keys": [
                {"user": "demo-dev-01", "access_key_id": "AKIA************SEED", "age_days": 142},
                {"user": "demo-ops-01", "access_key_id": "AKIA************DEMO", "age_days": 117},
            ],
            "max_age_days": 90,
        },
    ),
    (
        "aws.iam.admin_users",
        EvidenceStatus.WARN,
        {
            "admin_user_count": 2,
            "admin_users": ["demo-admin-01", "demo-ops-01"],
            "warn_limit": 3,
        },
    ),
    (
        "aws.cloudtrail.enabled",
        EvidenceStatus.PASS,
        {
            "trails": 1,
            "compliant_trails": ["demo-fintech-trail"],
            "multi_region": 1,
            "log_file_validation": 1,
        },
    ),
    (
        "aws.s3.public_block",
        EvidenceStatus.PASS,
        {"buckets": 6, "blocked": 6, "unblocked": [], "errors": []},
    ),
    (
        "aws.s3.encryption",
        EvidenceStatus.PASS,
        {"buckets": 6, "encrypted": 6, "unencrypted": [], "errors": []},
    ),
    (
        "aws.rds.encryption",
        EvidenceStatus.PASS,
        {
            "instances": 2,
            "compliant": 2,
            "violations": [],
            "min_backup_retention_days": 7,
        },
    ),
    (
        "aws.ec2.open_sg",
        EvidenceStatus.PASS,
        {"security_groups": 8, "open_rules": [], "checked_ports": [22, 3389, 3306, 5432]},
    ),
)

# 2차 스냅샷에서 달라지는 점검. 나머지는 1차와 완전히 같아서 변경 감지가 여기서만 뜬다.
DRIFTED_CHECK_ID = "aws.s3.public_block"
DRIFTED_RESULT: tuple[EvidenceStatus, dict[str, Any]] = (
    EvidenceStatus.FAIL,
    {"buckets": 6, "blocked": 5, "unblocked": [_UNBLOCKED_BUCKET], "errors": []},
)

# PRD §4 에서 클릭해 보는 항목. 시드가 끝나면 여기가 미충족이어야 한다.
SHOWCASE_CRITERION_CODE = "2.5.3"


class DemoSeedError(RuntimeError):
    """시드에 필요한 재료가 없거나 결과가 기대와 다를 때."""


@dataclass
class DemoSeedResult:
    """시드 1회 결과 요약. 콘솔 출력과 테스트가 함께 쓴다."""

    org_id: uuid.UUID
    project_id: uuid.UUID
    assessment_id: uuid.UUID
    draft_id: uuid.UUID
    user_count: int = 0
    criteria_count: int = 0
    document_count: int = 0
    chunk_count: int = 0
    evidence_count: int = 0
    snapshot_count: int = 0
    alert_count: int = 0
    finding_count: int = 0
    # 전체 준비도(0~100)와 장별 준비도(`summary_json` 그대로).
    readiness: float = 0.0
    by_chapter: dict[str, Any] = field(default_factory=dict)
    unmet_count: int = 0

    def __repr__(self) -> str:
        """디버깅용 표현."""
        return (
            f"DemoSeedResult(project_id={self.project_id}, documents={self.document_count}, "
            f"evidence={self.evidence_count}, findings={self.finding_count})"
        )


# --------------------------------------------------------------------------
# 정리(멱등)
# --------------------------------------------------------------------------


def _delete_objects(keys: list[str]) -> None:
    """S3 오브젝트를 지운다. 스토리지가 없어도 시드를 멈추지 않는다(잔재만 남는다)."""
    if not keys:
        return
    try:
        storage = get_storage()
        for key in keys:
            storage.delete_object(key)
    except StorageError:
        logger.warning("이전 데모 파일을 지우지 못했다. 오브젝트 잔재가 남는다", exc_info=True)


def purge_demo_data(db: Session) -> bool:
    """기존 데모 데이터를 전부 지운다. 지울 것이 있었으면 True.

    FK 를 역순으로 훑는다. ON DELETE 규칙에 기대지 않고 직접 지워서, 조직에 속하지
    않는 심사원·운영자 계정과 감사 로그까지 남김없이 정리한다. 커밋은 호출자가 한다.
    """
    org_id = db.execute(
        select(Organization.id).where(Organization.name == DEMO_ORG_NAME)
    ).scalar_one_or_none()

    platform_emails = [email for email, _, in_org in DEMO_ACCOUNTS if not in_org]
    if org_id is None:
        # 조직만 지워진 반쪽 상태를 대비해 플랫폼 계정은 항상 확인한다.
        leftovers = list(
            db.execute(select(User.id).where(User.email.in_(platform_emails))).scalars()
        )
        if leftovers:
            db.execute(delete(User).where(User.id.in_(leftovers)))
        return bool(leftovers)

    project_ids = list(
        db.execute(select(Project.id).where(Project.org_id == org_id)).scalars()
    )
    if project_ids:
        document_keys = list(
            db.execute(
                select(Document.s3_key).where(Document.project_id.in_(project_ids))
            ).scalars()
        )
        draft_keys = [
            key
            for key in db.execute(
                select(Draft.docx_s3_key).where(Draft.project_id.in_(project_ids))
            ).scalars()
            if key
        ]
        _delete_objects(document_keys + draft_keys)

        draft_ids = list(
            db.execute(select(Draft.id).where(Draft.project_id.in_(project_ids))).scalars()
        )
        assessment_ids = list(
            db.execute(
                select(Assessment.id).where(Assessment.project_id.in_(project_ids))
            ).scalars()
        )
        document_ids = list(
            db.execute(
                select(Document.id).where(Document.project_id.in_(project_ids))
            ).scalars()
        )

        if draft_ids:
            db.execute(delete(ReviewTask).where(ReviewTask.draft_id.in_(draft_ids)))
        if assessment_ids:
            db.execute(delete(Finding).where(Finding.assessment_id.in_(assessment_ids)))
        if document_ids:
            db.execute(delete(Chunk).where(Chunk.document_id.in_(document_ids)))
        # 알림이 증적을 참조하므로 증적보다 먼저 지운다.
        db.execute(delete(Alert).where(Alert.project_id.in_(project_ids)))
        db.execute(delete(Assessment).where(Assessment.project_id.in_(project_ids)))
        db.execute(delete(Document).where(Document.project_id.in_(project_ids)))
        db.execute(delete(Evidence).where(Evidence.project_id.in_(project_ids)))
        db.execute(delete(Connector).where(Connector.project_id.in_(project_ids)))
        db.execute(delete(Draft).where(Draft.project_id.in_(project_ids)))

    db.execute(delete(AuditLog).where(AuditLog.org_id == org_id))
    db.execute(delete(User).where(User.org_id == org_id))
    db.execute(delete(User).where(User.email.in_(platform_emails)))
    db.execute(delete(Project).where(Project.org_id == org_id))
    db.execute(delete(Organization).where(Organization.id == org_id))
    return True


# --------------------------------------------------------------------------
# 단계별 시드
# --------------------------------------------------------------------------


def _create_org_and_users(db: Session) -> tuple[Organization, dict[str, User]]:
    """조직 1개와 데모 계정 4개를 만든다."""
    org = Organization(name=DEMO_ORG_NAME, plan=OrgPlan.SIMPLIFIED)
    db.add(org)
    db.flush()

    users: dict[str, User] = {}
    for email, role, in_org in DEMO_ACCOUNTS:
        user = User(
            org_id=org.id if in_org else None,
            email=email,
            role=role,
            password_hash=hash_password(DEMO_PASSWORD),
        )
        db.add(user)
        users[email] = user
    db.flush()
    return org, users


def _create_project(db: Session, org: Organization) -> Project:
    """간편인증 프로젝트 1개를 만든다. 사후심사 기한은 오늘 + 312일이다."""
    project = Project(
        org_id=org.id,
        name=DEMO_PROJECT_NAME,
        cert_type=CertType.ISMS_P,
        is_simplified=True,
        scope_text=DEMO_SCOPE_TEXT,
        audit_due_date=date.today() + timedelta(days=AUDIT_DUE_IN_DAYS),
    )
    db.add(project)
    db.flush()
    return project


def sample_files(samples_dir: Path | None = None) -> list[Path]:
    """`data/samples/` 의 지원 확장자 파일을 이름순으로 모은다."""
    source = samples_dir or DEFAULT_SAMPLES_DIR
    if not source.is_dir():
        raise DemoSeedError(f"샘플 문서 폴더가 없다: {source}")
    files = sorted(
        path
        for path in source.iterdir()
        if path.is_file() and extension_of(path.name) in SUPPORTED_EXTENSIONS
    )
    if not files:
        raise DemoSeedError(
            f"샘플 문서가 없다: {source} (먼저 `uv run python ../../scripts/gen_samples.py`)"
        )
    return files


def _upload_and_ingest(
    db: Session, project: Project, samples_dir: Path | None = None
) -> tuple[int, int]:
    """샘플 문서를 S3 에 올리고 인제스트까지 끝낸다. `(문서 수, 청크 수)`.

    업로드 API 와 같은 순서(원문 S3 저장 → Document 행 → 인제스트)를 따르되,
    HTTP 를 거치지 않고 서비스 함수를 직접 부른다.
    """
    storage = get_storage()
    storage.ensure_bucket()

    documents = 0
    chunks = 0
    for path in sample_files(samples_dir):
        data = path.read_bytes()
        document_id = uuid.uuid4()
        s3_key = (
            f"orgs/{project.org_id}/projects/{project.id}/"
            f"documents/{document_id}/{path.name}"
        )
        mime = SUPPORTED_EXTENSIONS[extension_of(path.name)]
        storage.put_object(s3_key, data, mime)

        db.add(
            Document(
                id=document_id,
                project_id=project.id,
                filename=path.name,
                s3_key=s3_key,
                mime=mime,
                status=DocumentStatus.UPLOADED,
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )
        db.commit()

        result = run_ingest(document_id, db=db)
        chunks += result.chunk_count
        documents += 1

    failed = list(
        db.execute(
            select(Document.filename).where(
                Document.project_id == project.id,
                Document.status != DocumentStatus.PARSED,
            )
        ).scalars()
    )
    if failed:
        raise DemoSeedError(f"인제스트에 실패한 문서가 있다: {', '.join(failed)}")
    return documents, chunks


def _create_connector(db: Session, project: Project, collected_at: datetime) -> Connector:
    """AWS 커넥터 1개를 만든다. 자격증명은 더미이고 암호화해서 저장한다."""
    auth = AwsAuth(
        auth_type=AUTH_TYPE_ACCESS_KEY,
        region=DEMO_AWS_REGION,
        access_key_id=DEMO_ACCESS_KEY_ID,
        secret_access_key=DEMO_SECRET_ACCESS_KEY,
    )
    connector = Connector(
        project_id=project.id,
        type=ConnectorType.AWS,
        config_json=build_stored_config(auth, account_id_masked=DEMO_ACCOUNT_ID_MASKED),
        status=ConnectorStatus.CONNECTED,
        last_collected_at=collected_at,
    )
    db.add(connector)
    db.flush()
    return connector


def _snapshot_items(drifted: bool) -> dict[str, SnapshotItem]:
    """스냅샷 하나의 점검 결과 10개를 만든다. `drifted` 면 S3 차단 점검만 fail 이다."""
    items: dict[str, SnapshotItem] = {}
    for check_id, status, payload in BASE_SNAPSHOT:
        if drifted and check_id == DRIFTED_CHECK_ID:
            status, payload = DRIFTED_RESULT
        items[check_id] = SnapshotItem(
            check_id=check_id, status=status, payload=copy.deepcopy(payload)
        )
    return items


def _store_snapshot(
    db: Session,
    *,
    project: Project,
    connector: Connector,
    items: dict[str, SnapshotItem],
    mappings: dict[str, CheckMapping],
    collected_at: datetime,
) -> dict[str, Evidence]:
    """스냅샷 1회분을 `evidence` 에 넣는다. check_id → 저장된 행."""
    snapshot_id = uuid.uuid4().hex
    rows: dict[str, Evidence] = {}
    for check_id, item in items.items():
        mapping = mappings.get(check_id)
        if mapping is None:
            raise DemoSeedError(f"매핑에 없는 점검이다: {check_id}")
        row = Evidence(
            project_id=project.id,
            connector_id=connector.id,
            source=mapping.source,
            check_id=check_id,
            criterion_codes=list(mapping.criterion_codes),
            status=item.status,
            payload_json=copy.deepcopy(item.payload),
            collected_at=collected_at,
            snapshot_id=snapshot_id,
        )
        db.add(row)
        rows[check_id] = row
    db.flush()
    return rows


def _seed_evidence(
    db: Session, project: Project, *, now: datetime
) -> tuple[Connector, int, int]:
    """커넥터 1개 + 스냅샷 2회 + 변경 감지 알림을 만든다. `(커넥터, 증적 수, 알림 수)`.

    알림 문구는 지어내지 않고 `app/connectors/drift.py` 가 만든 것을 그대로 쓴다.
    """
    mappings = load_check_mappings()
    connector = _create_connector(db, project, now)

    first_at = now - FIRST_SNAPSHOT_AGO
    previous = _snapshot_items(drifted=False)
    _store_snapshot(
        db,
        project=project,
        connector=connector,
        items=previous,
        mappings=mappings,
        collected_at=first_at,
    )

    current = _snapshot_items(drifted=True)
    current_rows = _store_snapshot(
        db,
        project=project,
        connector=connector,
        items=current,
        mappings=mappings,
        collected_at=now,
    )

    changes: list[DriftChange] = detect_drift(previous, current, mappings=mappings)
    if not changes:
        raise DemoSeedError("변경 감지 알림이 만들어지지 않았다(스냅샷이 서로 같다)")
    for change in changes:
        row = current_rows.get(change.check_id)
        db.add(
            Alert(
                project_id=project.id,
                type=AlertType.DRIFT,
                message=change.message,
                evidence_id=row.id if row is not None else None,
                # 데모에서 "새 알림" 으로 보여야 하므로 미읽음으로 둔다.
                read_at=None,
            )
        )
    db.flush()
    return connector, len(previous) + len(current), len(changes)


def _run_assessment(db: Session, project: Project) -> Assessment:
    """모의심사 1회를 동기로 끝낸다. LLM 키가 없으면 FakeProvider 가 쓰인다."""
    assessment = Assessment(project_id=project.id, status=AssessmentStatus.QUEUED)
    db.add(assessment)
    db.commit()

    # `run_assessment` 는 자기 세션을 연다. 여기서 만든 행이 보이도록 먼저 커밋했다.
    result = run_assessment(assessment.id)
    if result.status is not AssessmentStatus.DONE:
        raise DemoSeedError(f"모의심사가 done 으로 끝나지 않았다: {result.status.value}")

    db.expire_all()
    return db.execute(
        select(Assessment).where(Assessment.id == assessment.id)
    ).scalar_one()


def _create_sow_draft(db: Session, project: Project, author: User) -> Draft:
    """운영명세서 초안 1개를 만들고 미배정 검수 과제를 올린다(초안 API 와 같은 흐름)."""
    content = build_sow_content(db, project)
    version = 1
    draft_id = uuid.uuid4()
    s3_key = draft_docx_key(
        org_id=project.org_id,
        project_id=project.id,
        draft_id=draft_id,
        kind=DraftKind.SOW,
        version=version,
    )
    store_draft_docx(s3_key, render_draft_docx(DraftKind.SOW, project, content, version))

    draft = Draft(
        id=draft_id,
        project_id=project.id,
        kind=DraftKind.SOW,
        version=version,
        # 생성 직후는 언제나 검수 대기다. 다운로드는 심사원 승인 뒤에 열린다.
        status=DraftStatus.IN_REVIEW,
        content_json=content,
        docx_s3_key=s3_key,
        created_by=author.id,
    )
    db.add(draft)
    ensure_review_task(db, draft)
    db.flush()
    return draft


# --------------------------------------------------------------------------
# 진입점
# --------------------------------------------------------------------------


def seed_demo(db: Session, *, samples_dir: Path | None = None) -> DemoSeedResult:
    """데모 데이터를 처음부터 다시 만든다. 여러 번 실행해도 결과가 같다."""
    purge_demo_data(db)
    db.commit()

    org, users = _create_org_and_users(db)
    project = _create_project(db, org)
    db.commit()

    seed_criteria(db)
    db.commit()

    document_count, chunk_count = _upload_and_ingest(db, project, samples_dir)

    now = datetime.now(UTC)
    _, evidence_count, alert_count = _seed_evidence(db, project, now=now)
    db.commit()

    assessment = _run_assessment(db, project)
    draft = _create_sow_draft(db, project, users[ADMIN_EMAIL])
    db.commit()

    summary = dict(assessment.summary_json or {})
    counts = summary.get("counts")
    unmet = int(counts.get("unmet", 0)) if isinstance(counts, dict) else 0
    by_chapter = summary.get("by_chapter")

    finding_count = int(
        db.execute(
            select(func.count())
            .select_from(Finding)
            .where(Finding.assessment_id == assessment.id)
        ).scalar_one()
    )

    return DemoSeedResult(
        org_id=org.id,
        project_id=project.id,
        assessment_id=assessment.id,
        draft_id=draft.id,
        user_count=len(users),
        criteria_count=count_criteria(db),
        document_count=document_count,
        chunk_count=chunk_count,
        evidence_count=evidence_count,
        snapshot_count=2,
        alert_count=alert_count,
        finding_count=finding_count,
        readiness=float(summary.get("readiness") or 0.0),
        by_chapter=by_chapter if isinstance(by_chapter, dict) else {},
        unmet_count=unmet,
    )


def showcase_finding(db: Session, assessment_id: uuid.UUID) -> Finding | None:
    """PRD §4 에서 보여 주는 항목(2.5.3)의 판정을 읽는다."""
    return db.execute(
        select(Finding).where(
            Finding.assessment_id == assessment_id,
            Finding.criterion_code == SHOWCASE_CRITERION_CODE,
        )
    ).scalar_one_or_none()


def drift_alert_message(db: Session, project_id: uuid.UUID) -> str | None:
    """프로젝트의 최신 변경 감지 알림 문구."""
    return db.execute(
        select(Alert.message)
        .where(Alert.project_id == project_id, Alert.type == AlertType.DRIFT)
        .order_by(Alert.created_at.desc(), Alert.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def pending_review_task_count(db: Session, project_id: uuid.UUID) -> int:
    """프로젝트에 딸린 대기 중 검수 과제 수."""
    return int(
        db.execute(
            select(func.count())
            .select_from(ReviewTask)
            .join(Draft, Draft.id == ReviewTask.draft_id)
            .where(Draft.project_id == project_id, ReviewTask.status == ReviewTaskStatus.PENDING)
        ).scalar_one()
    )
