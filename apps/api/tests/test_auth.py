"""로그인·로그아웃·세션 테스트."""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import AuditLog, UserRole
from app.services.demo_accounts import DEMO_MEMBER_EMAIL
from tests.conftest import TEST_PASSWORD, login, make_org, make_user


def test_login_success_sets_session_cookie(client, db):
    """올바른 자격이면 200 과 세션 쿠키를 준다."""
    org = make_org(db, "A조직")
    make_user(db, email="admin-a@example.com", role=UserRole.ORG_ADMIN, org_id=org.id)

    response = login(client, "admin-a@example.com")

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "admin-a@example.com"
    assert body["role"] == "org_admin"
    assert "password_hash" not in body
    assert get_settings().session_cookie_name in response.cookies


def test_login_normalizes_email_case(client, db):
    """이메일 대소문자·공백은 정규화한다."""
    org = make_org(db, "A조직")
    make_user(db, email="admin-a@example.com", role=UserRole.ORG_ADMIN, org_id=org.id)

    response = login(client, "  Admin-A@Example.com ")

    assert response.status_code == 200


def test_login_failure_returns_401_and_is_audited(client, db):
    """비밀번호가 틀리면 401 이고 실패도 감사 로그에 남는다."""
    org = make_org(db, "A조직")
    make_user(db, email="admin-a@example.com", role=UserRole.ORG_ADMIN, org_id=org.id)

    response = login(client, "admin-a@example.com", password="wrong-password")

    assert response.status_code == 401
    logs = db.execute(select(AuditLog).where(AuditLog.action == "login")).scalars().all()
    assert len(logs) == 1
    assert logs[0].meta_json["result"] == "failure"
    # 감사 로그에 비밀번호가 남으면 안 된다.
    assert "wrong-password" not in str(logs[0].meta_json)


def test_login_unknown_email_returns_401(client, db):
    """없는 계정도 같은 문구로 401 이다(존재 여부를 흘리지 않는다)."""
    response = login(client, "nobody@example.com")

    assert response.status_code == 401
    logs = db.execute(select(AuditLog).where(AuditLog.action == "login")).scalars().all()
    assert len(logs) == 1
    assert logs[0].user_id is None


def test_login_success_is_audited(client, db):
    """성공 로그인도 감사 로그에 남는다."""
    org = make_org(db, "A조직")
    user = make_user(db, email="admin-a@example.com", role=UserRole.ORG_ADMIN, org_id=org.id)

    assert login(client, "admin-a@example.com").status_code == 200

    log = db.execute(select(AuditLog).where(AuditLog.action == "login")).scalar_one()
    assert log.user_id == user.id
    assert log.org_id == org.id
    assert log.meta_json["result"] == "success"


def test_me_requires_session(client):
    """세션이 없으면 /auth/me 는 401 이다."""
    assert client.get("/auth/me").status_code == 401


def test_me_returns_current_user(client, db):
    """로그인 후 /auth/me 는 현재 사용자를 준다."""
    user = make_user(db, email="operator@example.com", role=UserRole.OPERATOR)
    login(client, "operator@example.com")

    response = client.get("/auth/me")

    assert response.status_code == 200
    assert response.json()["id"] == str(user.id)
    assert response.json()["org_id"] is None


def test_logout_clears_session(client, db):
    """로그아웃하면 세션이 끊긴다."""
    org = make_org(db, "A조직")
    make_user(db, email="admin-a@example.com", role=UserRole.ORG_ADMIN, org_id=org.id)
    login(client, "admin-a@example.com")

    assert client.post("/auth/logout").status_code == 200
    assert client.get("/auth/me").status_code == 401


def test_tampered_session_cookie_is_rejected(client, db):
    """서명이 깨진 쿠키는 401 이다."""
    org = make_org(db, "A조직")
    make_user(db, email="admin-a@example.com", role=UserRole.ORG_ADMIN, org_id=org.id)
    login(client, "admin-a@example.com")

    client.cookies.set(get_settings().session_cookie_name, "forged.session.value")

    assert client.get("/auth/me").status_code == 401


def test_password_hash_is_not_plaintext(db):
    """저장된 값은 bcrypt 해시여야 한다."""
    user = make_user(db, email="operator@example.com", role=UserRole.OPERATOR)

    assert user.password_hash != TEST_PASSWORD
    assert user.password_hash.startswith("$2")


# ---------------------------------------------------------------------------
# 데모 체험 로그인 (POST /auth/demo-login)
# ---------------------------------------------------------------------------


@pytest.fixture
def demo_login_enabled(monkeypatch):
    """`DEMO_LOGIN_ENABLED=true` 로 설정을 갈아끼운다(설정은 lru_cache 라 비워 준다)."""
    monkeypatch.setenv("DEMO_LOGIN_ENABLED", "true")
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("DEMO_LOGIN_ENABLED", raising=False)
    get_settings.cache_clear()


def make_demo_member(db):
    """시드된 데모 org_member 계정과 같은 이메일·역할의 사용자를 만든다."""
    org = make_org(db, "데모핀테크")
    return make_user(db, email=DEMO_MEMBER_EMAIL, role=UserRole.ORG_MEMBER, org_id=org.id)


def test_demo_login_disabled_by_default_returns_404(client, db):
    """기본값에서는 엔드포인트가 없는 것처럼 404 다(기능 존재를 흘리지 않는다)."""
    make_demo_member(db)

    response = client.post("/auth/demo-login")

    assert response.status_code == 404
    assert get_settings().session_cookie_name not in response.cookies


def test_demo_login_returns_member_session(client, db, demo_login_enabled):
    """켜져 있고 계정이 있으면 200 · 세션 쿠키 · org_member 를 준다."""
    user = make_demo_member(db)

    response = client.post("/auth/demo-login")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(user.id)
    assert body["role"] == "org_member"
    assert "password_hash" not in body
    assert get_settings().session_cookie_name in response.cookies

    # 발급된 세션으로 곧바로 인증된 요청이 된다.
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == DEMO_MEMBER_EMAIL


def test_demo_login_without_seed_returns_404(client, db, demo_login_enabled):
    """켜져 있어도 시드가 없으면 404 + 안내 문구다."""
    response = client.post("/auth/demo-login")

    assert response.status_code == 404
    assert "데모 데이터" in response.json()["detail"]


def test_demo_login_wrong_role_returns_404(client, db, demo_login_enabled):
    """같은 이메일이라도 역할이 org_member 가 아니면 체험을 열지 않는다."""
    org = make_org(db, "데모핀테크")
    make_user(db, email=DEMO_MEMBER_EMAIL, role=UserRole.ORG_ADMIN, org_id=org.id)

    response = client.post("/auth/demo-login")

    assert response.status_code == 404
    assert get_settings().session_cookie_name not in response.cookies


def test_demo_login_is_audited_with_demo_flag(client, db, demo_login_enabled):
    """감사 로그에 demo 플래그가 남는다(일반 로그인과 구분한다)."""
    user = make_demo_member(db)

    assert client.post("/auth/demo-login").status_code == 200

    log = db.execute(select(AuditLog).where(AuditLog.action == "login")).scalar_one()
    assert log.user_id == user.id
    assert log.org_id == user.org_id
    assert log.meta_json["result"] == "success"
    assert log.meta_json["demo"] is True
