"""ORM 모델 패키지.

Alembic 자동 생성과 매퍼 설정이 모든 모델을 보도록, 여기서 전부 임포트한다.
ORM relationship 은 일부러 두지 않는다. 모든 조회는 org_id 스코프를 명시한
조인으로 작성해 테넌트 격리를 코드에서 눈으로 확인할 수 있게 한다.
"""

from app.models.assessment import Assessment, Finding
from app.models.audit import Alert, AuditLog
from app.models.connector import Connector, Evidence
from app.models.criterion import Criterion
from app.models.document import EMBEDDING_DIM, Chunk, Document
from app.models.draft import Draft, ReviewTask
from app.models.enums import (
    AlertType,
    AssessmentStatus,
    CertType,
    ConnectorStatus,
    ConnectorType,
    DecidedBy,
    DocumentStatus,
    DraftKind,
    DraftStatus,
    EvidenceStatus,
    FindingStatus,
    OrgPlan,
    ReviewTaskStatus,
    UserRole,
)
from app.models.organization import Organization, User
from app.models.project import Project

__all__ = [
    "EMBEDDING_DIM",
    "Alert",
    "AlertType",
    "Assessment",
    "AssessmentStatus",
    "AuditLog",
    "CertType",
    "Chunk",
    "Connector",
    "ConnectorStatus",
    "ConnectorType",
    "Criterion",
    "DecidedBy",
    "Document",
    "DocumentStatus",
    "Draft",
    "DraftKind",
    "DraftStatus",
    "Evidence",
    "EvidenceStatus",
    "Finding",
    "FindingStatus",
    "OrgPlan",
    "Organization",
    "Project",
    "ReviewTask",
    "ReviewTaskStatus",
    "User",
    "UserRole",
]
