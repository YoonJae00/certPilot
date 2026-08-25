"""ISMS-P 인증기준 지식베이스 모델.

본문은 반드시 `data/criteria/criteria.json` 에서 로드한다(CLAUDE.md 절대 규칙 1).
이 모델은 그 적재 대상 스키마만 정의한다.
"""

from typing import Any

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Criterion(Base):
    """인증기준 항목 1개. 코드(예: `2.5.3`)가 자연 키다."""

    __tablename__ = "criteria"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    chapter: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    section: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    requirement: Mapped[str] = mapped_column(Text, nullable=False)
    # 문자열 배열은 모두 JSONB 로 저장한다(스키마 변경 없이 항목 확장 가능).
    checkpoints: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    defect_examples: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_hints: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    is_simplified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False, default="2023")
