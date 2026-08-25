"""인증기준 지식베이스 적재.

CLAUDE.md 절대 규칙 1: 항목 코드·명칭·본문은 지어내지 않고 항상
`data/criteria/criteria.json` 에서 읽는다. 이 모듈은 그 JSON 을 `criteria` 테이블로
그대로 옮기기만 한다(내용 가공 없음).
"""

import json
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Criterion

# apps/api/app/services/criteria_loader.py -> 리포 루트
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CRITERIA_PATH = REPO_ROOT / "data" / "criteria" / "criteria.json"

_REQUIRED_KEYS = ("code", "chapter", "section", "title", "requirement")


class CriteriaLoadError(RuntimeError):
    """지식베이스 파일이 없거나 형식이 어긋날 때."""


def load_criteria_file(path: Path | None = None) -> tuple[str, list[dict[str, Any]]]:
    """criteria.json 을 읽어 `(version, items)` 를 돌려준다."""
    source_path = path or DEFAULT_CRITERIA_PATH
    if not source_path.exists():
        raise CriteriaLoadError(
            f"인증기준 파일이 없다: {source_path} (먼저 `make kb` 로 생성한다)"
        )

    with source_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise CriteriaLoadError(f"인증기준 파일 형식이 어긋난다: {source_path}")

    version = str(payload.get("version") or "2023")
    items: list[dict[str, Any]] = payload["items"]
    for item in items:
        missing = [key for key in _REQUIRED_KEYS if not item.get(key)]
        if missing:
            raise CriteriaLoadError(
                f"항목 {item.get('code', '?')} 에 필수 키가 없다: {', '.join(missing)}"
            )
    return version, items


def seed_criteria(
    db: Session, *, path: Path | None = None, prune: bool = True
) -> int:
    """criteria.json 을 `criteria` 테이블에 upsert 한다. 적재된 행 수를 돌려준다.

    `prune=True` 면 JSON 에 없는 코드는 지운다(안내서 개정 시 잔재를 남기지 않는다).
    커밋은 호출자가 한다.
    """
    version, items = load_criteria_file(path)

    rows = [
        {
            "code": str(item["code"]),
            "chapter": int(item["chapter"]),
            "section": str(item["section"]),
            "title": str(item["title"]),
            "requirement": str(item["requirement"]),
            "checkpoints": list(item.get("checkpoints") or []),
            "defect_examples": list(item.get("defect_examples") or []),
            "evidence_hints": list(item.get("evidence_hints") or []),
            "is_simplified": bool(item.get("is_simplified", False)),
            "version": version,
        }
        for item in items
    ]

    statement = insert(Criterion).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[Criterion.code],
        set_={
            column: statement.excluded[column]
            for column in (
                "chapter",
                "section",
                "title",
                "requirement",
                "checkpoints",
                "defect_examples",
                "evidence_hints",
                "is_simplified",
                "version",
            )
        },
    )
    db.execute(statement)

    if prune:
        known = {row["code"] for row in rows}
        db.execute(delete(Criterion).where(Criterion.code.notin_(known)))

    return len(rows)


def count_criteria(db: Session) -> int:
    """적재된 항목 수."""
    return int(db.execute(select(func.count()).select_from(Criterion)).scalar_one())
