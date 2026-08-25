"""data/criteria/criteria.json → `criteria` 테이블 적재 CLI.

실행:
    cd apps/api && uv run python ../../scripts/seed_criteria.py

`.env` 의 `DATABASE_URL` 을 쓴다. 여러 번 실행해도 결과가 같다(upsert).
"""

from __future__ import annotations

import sys
from pathlib import Path

# apps/api 를 임포트 경로에 넣는다. 리포 어디서 실행해도 동작하게 하기 위함이다.
REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.db import get_session_factory  # noqa: E402
from app.services.criteria_loader import count_criteria, seed_criteria  # noqa: E402


def main() -> int:
    """인증기준을 적재하고 결과를 출력한다."""
    session = get_session_factory()()
    try:
        loaded = seed_criteria(session)
        session.commit()
        total = count_criteria(session)
    finally:
        session.close()

    print(f"인증기준 적재 완료: {loaded}개 upsert, 테이블 총 {total}행")
    return 0 if loaded == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
