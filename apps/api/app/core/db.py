"""SQLAlchemy 2 엔진·세션 설정.

엔진은 지연 생성한다. DB가 없어도 `/health` 같은 엔드포인트와 테스트는
동작해야 하므로 임포트 시점에 커넥션을 열지 않는다.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    """모든 ORM 모델의 공통 베이스."""


@lru_cache
def get_engine() -> Engine:
    """엔진 싱글턴. 첫 호출 때만 만든다."""
    settings = get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """세션 팩토리 싱글턴."""
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI 의존성. 요청 단위로 세션을 열고 반드시 닫는다."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
