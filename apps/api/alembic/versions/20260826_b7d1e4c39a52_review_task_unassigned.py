"""review_tasks.reviewer_id 미배정 허용

초안이 만들어지면 심사원이 정해지기 전에 검수 과제가 먼저 큐에 올라간다(PRD §7 F6).
미배정 상태를 표현해야 해서 `reviewer_id` 를 NULL 허용으로 바꾼다.

Revision ID: b7d1e4c39a52
Revises: fbe7c9c74dc5
Create Date: 2026-08-26 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7d1e4c39a52"
down_revision: str | None = "fbe7c9c74dc5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """미배정 검수 과제를 허용한다."""
    op.alter_column("review_tasks", "reviewer_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    """되돌릴 때는 미배정 과제를 지운 뒤 NOT NULL 을 복원한다."""
    op.execute("DELETE FROM review_tasks WHERE reviewer_id IS NULL")
    op.alter_column("review_tasks", "reviewer_id", existing_type=sa.Uuid(), nullable=False)
