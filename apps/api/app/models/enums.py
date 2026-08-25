"""도메인 열거형.

DB 에는 네이티브 PostgreSQL enum 타입을 만들지 않고, VARCHAR + CHECK 제약으로
저장한다(`native_enum=False`). 값이 늘어날 때 마이그레이션이 단순해지고,
파이썬 쪽에서는 Enum 타입 안전성을 그대로 얻는다. 프로젝트 전체가 이 방식을 따른다.
"""

import enum

from sqlalchemy import Enum as SAEnum


class OrgPlan(enum.StrEnum):
    """조직 요금제. 간편인증 대상 여부와 연결된다."""

    SIMPLIFIED = "simplified"
    STANDARD = "standard"


class UserRole(enum.StrEnum):
    """PRD §3 역할."""

    ORG_ADMIN = "org_admin"
    ORG_MEMBER = "org_member"
    REVIEWER = "reviewer"
    OPERATOR = "operator"


class CertType(enum.StrEnum):
    """인증 종류."""

    ISMS = "ISMS"
    ISMS_P = "ISMS-P"


class DocumentStatus(enum.StrEnum):
    """업로드 문서의 파싱 상태."""

    UPLOADED = "uploaded"
    PARSED = "parsed"
    FAILED = "failed"


class ConnectorType(enum.StrEnum):
    """증적 커넥터 종류."""

    AWS = "aws"
    GITHUB = "github"


class ConnectorStatus(enum.StrEnum):
    """커넥터 연결 상태."""

    PENDING = "pending"
    CONNECTED = "connected"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class EvidenceStatus(enum.StrEnum):
    """증적 점검 결과."""

    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    UNKNOWN = "unknown"


class AssessmentStatus(enum.StrEnum):
    """모의심사 실행 상태."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class FindingStatus(enum.StrEnum):
    """항목별 판정."""

    MET = "met"
    PARTIAL = "partial"
    UNMET = "unmet"
    UNKNOWN = "unknown"


class DecidedBy(enum.StrEnum):
    """판정 주체."""

    RULE = "rule"
    LLM = "llm"
    REVIEWER = "reviewer"


class DraftKind(enum.StrEnum):
    """초안 종류. sow = 운영명세서."""

    SOW = "sow"
    POLICY = "policy"


class DraftStatus(enum.StrEnum):
    """초안 승인 상태. approved 여야만 다운로드가 열린다."""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    RETURNED = "returned"


class ReviewTaskStatus(enum.StrEnum):
    """검수 과제 상태."""

    PENDING = "pending"
    APPROVED = "approved"
    RETURNED = "returned"


class AlertType(enum.StrEnum):
    """대시보드 알림 종류."""

    DRIFT = "drift"
    DUE = "due"
    DEFECT = "defect"


def enum_column(enum_cls: type[enum.Enum], name: str) -> SAEnum:
    """Enum 컬럼 타입을 만든다. 값(문자열)을 그대로 저장하고 CHECK 제약을 건다."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )
