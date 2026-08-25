"""데모 시드 CLI — PRD §4 3분 시나리오를 클린 DB 에서 재현할 상태를 만든다.

실행:
    cd apps/api && uv run python ../../scripts/seed_demo.py

`.env` 의 `DATABASE_URL`·`S3_*` 를 쓴다. 여러 번 실행해도 결과가 같다(기존 "데모핀테크"
데이터를 지우고 다시 만든다). 스키마는 미리 `alembic upgrade head` 로 맞춰 둔다.

실제 개인정보·실제 클라우드 자격증명은 들어가지 않는다. 전부 지어낸 더미 값이다.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# apps/api 를 임포트 경로에 넣는다. 리포 어디서 실행해도 동작하게 하기 위함이다.
REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.db import get_session_factory  # noqa: E402
from app.services.demo_seed import (  # noqa: E402
    ADMIN_EMAIL,
    AUDIT_DUE_IN_DAYS,
    DEMO_ACCOUNTS,
    DEMO_PASSWORD,
    DEMO_PROJECT_NAME,
    SHOWCASE_CRITERION_CODE,
    DemoSeedError,
    DemoSeedResult,
    drift_alert_message,
    pending_review_task_count,
    seed_demo,
    showcase_finding,
)

WEB_URL = "http://localhost:3000"
API_URL = "http://localhost:8000"

ROLE_LABELS = {
    "org_admin": "조직 관리자",
    "org_member": "조직 담당자",
    "reviewer": "심사원",
    "operator": "운영자",
}


def _percent(value: object) -> str:
    """준비도(0~1)를 백분율 문구로 바꾼다."""
    try:
        return f"{round(float(value or 0.0) * 100, 1)}%"
    except (TypeError, ValueError):
        return "-"


def _print_guide(result: DemoSeedResult, *, showcase: str, alert: str | None, tasks: int) -> None:
    """데모 진행에 필요한 계정·URL·시나리오를 출력한다."""
    line = "─" * 72
    print()
    print(line)
    print("  데모 시드 완료 — 데모핀테크")
    print(line)
    print()
    print("  [생성 결과]")
    print("    조직          : 데모핀테크 (간편인증)")
    print(f"    프로젝트      : {DEMO_PROJECT_NAME}")
    print(f"    사용자        : {result.user_count}명")
    print(f"    인증기준      : {result.criteria_count}개 항목")
    print(
        f"    문서          : {result.document_count}개 "
        f"(청크 {result.chunk_count}개, 전부 파싱 완료)"
    )
    print(
        f"    AWS 증적      : {result.evidence_count}건 "
        f"(스냅샷 {result.snapshot_count}회 × 점검 10개)"
    )
    print(f"    변경 감지     : 알림 {result.alert_count}건 (미읽음)")
    print(
        f"    모의심사      : 판정 {result.finding_count}개 "
        f"(미충족 {result.unmet_count}개, 전체 준비도 {_percent(result.readiness)})"
    )
    for chapter in sorted(result.by_chapter):
        bucket = result.by_chapter[chapter]
        print(f"                    {chapter}장 준비도 {_percent(bucket.get('readiness'))}")
    print(f"    운영명세서    : 초안 1개 (검수 대기), 검수 과제 {tasks}건")
    print()
    print("  [데모 계정] 비밀번호는 전부 " + DEMO_PASSWORD)
    for email, role, in_org in DEMO_ACCOUNTS:
        scope = "데모핀테크 소속" if in_org else "조직 무소속(플랫폼 계정)"
        print(f"    {email:<26} {ROLE_LABELS.get(role.value, role.value):<10} {scope}")
    print()
    print("  [접속 주소]")
    print(f"    웹        : {WEB_URL}   (터미널에서 `make web`)")
    print(f"    API 문서  : {API_URL}/docs   (터미널에서 `make api`)")
    print()
    print("  [3분 시나리오]")
    print(f"    0:00  {ADMIN_EMAIL} 로 로그인 → 프로젝트 '{DEMO_PROJECT_NAME}' 열기")
    print(f"    0:20  문서 탭 — 샘플 {result.document_count}개가 '파싱 완료' 로 보인다")
    print("    0:50  증적 탭 — AWS 커넥터 연결됨, 점검 10개와 항목 매핑 확인")
    print("    1:10  모의심사 탭 — 최근 실행 결과와 장별 준비도 확인")
    print(f"    1:40  판정 테이블에서 {showcase} 항목 열기 — 클라우드 증적·문서 근거 확인")
    print("    2:10  문서 탭 — 운영명세서 초안이 '검수 대기', 다운로드 버튼은 비활성")
    print("    2:30  reviewer@certpilot.kr 로 로그인 → 검수 큐에서 초안 열기 → 승인")
    print(f"    2:50  대시보드 — 변경 감지 알림과 사후심사 D-{AUDIT_DUE_IN_DAYS}")
    if alert:
        print(f"          알림 문구: {alert}")
    print()
    print(line)
    print()


def main() -> int:
    """시드를 실행하고 안내를 출력한다."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    print("데모 시드를 적재한다. 기존 '데모핀테크' 데이터는 지우고 다시 만든다…")
    session = get_session_factory()()
    try:
        result = seed_demo(session)
        finding = showcase_finding(session, result.assessment_id)
        alert = drift_alert_message(session, result.project_id)
        tasks = pending_review_task_count(session, result.project_id)
    except DemoSeedError as error:
        print(f"데모 시드에 실패했다: {error}", file=sys.stderr)
        return 1
    finally:
        session.close()

    showcase = SHOWCASE_CRITERION_CODE
    if finding is not None:
        showcase = f"{SHOWCASE_CRITERION_CODE}({finding.status.value})"
    _print_guide(result, showcase=showcase, alert=alert, tasks=tasks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
