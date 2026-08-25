"""테스트 공용 픽스처.

`certpilot_test` DB 를 만들고 실제 Alembic 마이그레이션을 적용한다. 테스트마다
모든 테이블을 TRUNCATE 해 서로 영향을 주지 않게 한다.

여기서 쓰는 비밀번호는 전부 가짜 값이다. 실제 자격증명을 넣지 않는다.
"""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import Settings, get_settings
from app.core.db import Base, get_engine, get_session_factory
from app.core.security import hash_password
from app.main import app
from app.models import CertType, Organization, Project, User, UserRole
from app.services.storage import ObjectStorage, get_storage

# moto 는 기본적으로 AWS 도메인만 가로챈다. MinIO 처럼 커스텀 엔드포인트를 쓰면
# 이 환경 변수로 알려 줘야 한다. moto 를 임포트하기 전에 설정해야 하므로 여기 둔다.
os.environ.setdefault("MOTO_S3_CUSTOM_ENDPOINTS", Settings().s3_endpoint)

API_ROOT = Path(__file__).resolve().parents[1]

# 픽스처 전용 가짜 비밀번호.
TEST_PASSWORD = "fixture-password-1234"


def _switch_to_test_database() -> str:
    """`DATABASE_URL` 을 테스트 DB 로 바꾼다(환경 변수가 .env 보다 우선한다).

    앱 설정은 요청 시점에 처음 읽히고 세션 픽스처에서 캐시를 비우므로,
    임포트 순서와 무관하게 테스트 DB 가 적용된다.
    """
    url = make_url(Settings().database_url)
    database = url.database or "certpilot"
    if not database.endswith("_test"):
        url = url.set(database=f"{database}_test")
    rendered = url.render_as_string(hide_password=False)
    os.environ["DATABASE_URL"] = rendered
    return rendered


TEST_DATABASE_URL = _switch_to_test_database()


def _create_test_database_if_missing() -> None:
    """테스트 DB 가 없으면 만든다. 유지보수 DB(postgres)로 붙어서 실행한다."""
    url = make_url(TEST_DATABASE_URL)
    engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            ).scalar_one_or_none()
            if exists is None:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        engine.dispose()


def _reset_schema_and_migrate() -> None:
    """스키마를 비우고 `alembic upgrade head` 를 그대로 적용한다."""
    engine = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()

    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.upgrade(config, "head")


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[None]:
    """세션 시작 시 테스트 DB 를 준비한다."""
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    _create_test_database_if_missing()
    _reset_schema_and_migrate()
    yield
    get_engine().dispose()


@pytest.fixture(autouse=True)
def _clean_tables(_database: None) -> Iterator[None]:
    """테스트마다 모든 테이블을 비운다."""
    names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with get_engine().begin() as conn:
        # 다른 연결이 락을 쥐고 있으면 무한 대기 대신 즉시 실패하게 한다.
        conn.execute(text("SET lock_timeout = '10s'"))
        conn.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def db() -> Iterator[Session]:
    """테스트에서 직접 데이터를 넣을 때 쓰는 세션."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def storage() -> Iterator[ObjectStorage]:
    """moto 로 가짜 S3 를 띄운다. 실제 MinIO 에는 아무것도 남지 않는다."""
    from moto import mock_aws

    with mock_aws():
        get_storage.cache_clear()
        bucket = get_storage()
        bucket.ensure_bucket()
        try:
            yield bucket
        finally:
            get_storage.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """쿠키를 유지하는 HTTP 클라이언트."""
    with TestClient(app) as test_client:
        yield test_client


def make_org(db: Session, name: str) -> Organization:
    """테스트용 조직을 만든다."""
    org = Organization(name=name)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def make_user(
    db: Session,
    *,
    email: str,
    role: UserRole,
    org_id: uuid.UUID | None = None,
) -> User:
    """테스트용 사용자를 만든다."""
    user = User(org_id=org_id, email=email, role=role, password_hash=hash_password(TEST_PASSWORD))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_project(db: Session, org_id: uuid.UUID, name: str) -> Project:
    """테스트용 프로젝트를 만든다."""
    project = Project(org_id=org_id, name=name, cert_type=CertType.ISMS_P, is_simplified=True)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def login(client: TestClient, email: str, password: str = TEST_PASSWORD):
    """로그인 요청 헬퍼. 성공하면 클라이언트에 세션 쿠키가 남는다."""
    return client.post("/auth/login", json={"email": email, "password": password})


@pytest.fixture
def tenants(db: Session) -> dict[str, object]:
    """A·B 두 조직과 역할별 사용자, 조직별 프로젝트 1개씩."""
    org_a = make_org(db, "A조직")
    org_b = make_org(db, "B조직")
    return {
        "org_a": org_a,
        "org_b": org_b,
        "admin_a": make_user(
            db, email="admin-a@example.com", role=UserRole.ORG_ADMIN, org_id=org_a.id
        ),
        "member_a": make_user(
            db, email="member-a@example.com", role=UserRole.ORG_MEMBER, org_id=org_a.id
        ),
        "admin_b": make_user(
            db, email="admin-b@example.com", role=UserRole.ORG_ADMIN, org_id=org_b.id
        ),
        "reviewer": make_user(db, email="reviewer@example.com", role=UserRole.REVIEWER),
        "operator": make_user(db, email="operator@example.com", role=UserRole.OPERATOR),
        "project_a": make_project(db, org_a.id, "A프로젝트"),
        "project_b": make_project(db, org_b.id, "B프로젝트"),
    }
