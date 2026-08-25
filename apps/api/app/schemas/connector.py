"""증적 커넥터 API 스키마 (PRD §7 F5).

요청에 들어오는 자격증명은 `SecretStr` 로 받는다. 응답 모델에는 자격증명을 담는
필드가 아예 없다 — 나갈 수 있는 값은 마스킹된 요약뿐이다(PRD §10).
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.connectors.aws import DEFAULT_REGION
from app.models import ConnectorStatus, ConnectorType, EvidenceStatus


class AwsConnectorConfigIn(BaseModel):
    """AWS 커넥터 연결 설정 입력.

    `role`       : 운영 기본. 고객 계정의 읽기 전용 역할을 외부 ID 로 AssumeRole 한다.
    `access_key` : 개발·데모 전용.
    """

    auth_type: Literal["role", "access_key"]
    region: str = Field(default=DEFAULT_REGION, min_length=1, max_length=32)
    role_arn: str | None = Field(default=None, max_length=2048)
    external_id: SecretStr | None = None
    access_key_id: str | None = Field(default=None, max_length=128)
    secret_access_key: SecretStr | None = None

    @model_validator(mode="after")
    def _check_required(self) -> "AwsConnectorConfigIn":
        """연결 방식별 필수 값을 확인한다."""
        if self.auth_type == "role":
            if not (self.role_arn or "").strip():
                raise ValueError("역할 연결에는 role_arn 이 필요하다")
            if self.external_id is None or not self.external_id.get_secret_value().strip():
                raise ValueError("역할 연결에는 external_id 가 필요하다")
        else:
            if not (self.access_key_id or "").strip():
                raise ValueError("액세스 키 연결에는 access_key_id 가 필요하다")
            if (
                self.secret_access_key is None
                or not self.secret_access_key.get_secret_value().strip()
            ):
                raise ValueError("액세스 키 연결에는 secret_access_key 가 필요하다")
        return self


class ConnectorCreate(BaseModel):
    """커넥터 생성 요청."""

    type: ConnectorType = ConnectorType.AWS
    config: AwsConnectorConfigIn


class ConnectorConfigSummary(BaseModel):
    """목록·상세에 나가는 설정 요약. 자격증명은 없고 계정 ID·ARN 은 마스킹돼 있다."""

    auth_type: str
    region: str
    role_arn_masked: str | None = None
    access_key_id_masked: str | None = None
    account_id_masked: str | None = None


class ConnectorOut(BaseModel):
    """커넥터 응답."""

    id: uuid.UUID
    project_id: uuid.UUID
    type: ConnectorType
    status: ConnectorStatus
    last_collected_at: datetime | None
    created_at: datetime
    config: ConnectorConfigSummary
    # 연결 테스트가 실패했을 때만 채워진다(자격증명 없이 사유 코드만).
    error_reason: str | None = None


class CollectResponse(BaseModel):
    """수동 수집 응답. 큐에 넣기만 한 경우 `snapshot_id` 는 비어 있다."""

    connector_id: uuid.UUID
    # queued = 워커가 처리 중, done = 요청 스레드에서 동기로 끝냄.
    state: Literal["queued", "done"]
    snapshot_id: str | None = None
    evidence_count: int = 0
    alert_count: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)


class EvidenceOut(BaseModel):
    """증적 1건. 매핑 파일의 표시명·pass 조건을 함께 실어 준다."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    connector_id: uuid.UUID | None
    source: str
    check_id: str
    criterion_codes: list[str] = Field(default_factory=list)
    status: EvidenceStatus
    payload_json: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime
    snapshot_id: str | None = None
    title: str | None = None
    pass_condition: str | None = None


class LatestEvidenceResponse(BaseModel):
    """프로젝트 최신 증적(check_id 별)."""

    project_id: uuid.UUID
    snapshot_id: str | None = None
    collected_at: datetime | None = None
    items: list[EvidenceOut] = Field(default_factory=list)
