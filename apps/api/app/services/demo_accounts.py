"""데모 계정 상수만 담는 얕은 모듈.

`demo_seed` 는 boto3 커넥터·워커·DOCX 생성까지 끌어오는 무거운 모듈이라, 이메일
상수 하나 때문에 API 라우터가 그 전부를 임포트하게 둘 수 없다. 그래서 상수만
여기로 분리하고 `demo_seed` 가 다시 가져다 쓴다(기존 임포트 경로는 그대로 동작한다).

여기 값은 전부 지어낸 더미다. 실제 개인정보·자격증명이 아니다(CLAUDE.md 절대 규칙 3).
"""

from app.models import UserRole

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

# 데모 체험 로그인(`POST /auth/demo-login`)이 세션을 발급하는 계정.
# 열람 위주의 org_member 를 쓴다 — 방문자가 시드 데이터를 망가뜨리지 않게 한다.
DEMO_MEMBER_EMAIL = DEMO_ACCOUNTS[1][0]
