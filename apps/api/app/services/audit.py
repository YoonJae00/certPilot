"""감사 로그 기록.

PRD §10: 로그인, 다운로드, 승인, 커넥터 변경, 역할 변경은 반드시 남긴다.
`meta` 에 비밀번호·토큰·자격증명을 넣지 않는다.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def record_audit(
    db: Session,
    *,
    action: str,
    org_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    target: str | None = None,
    meta: dict[str, Any] | None = None,
) -> AuditLog:
    """감사 로그 1건을 세션에 추가한다. 커밋은 호출자가 한다."""
    log = AuditLog(
        org_id=org_id,
        user_id=user_id,
        action=action,
        target=target,
        meta_json=meta or {},
    )
    db.add(log)
    return log
