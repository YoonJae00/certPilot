"""애플리케이션 설정. `.env` 파일과 환경 변수에서 값을 읽는다."""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 리포지토리 루트의 `.env` 를 기본으로 읽고, apps/api/.env 가 있으면 그걸로 덮어쓴다.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_API_ROOT = Path(__file__).resolve().parents[2]

# 로컬·테스트 전용 세션 서명 키. 실제 비밀이 아니며 배포에서는 반드시 덮어쓴다.
DEV_SESSION_SECRET = "dev-only-insecure-session-secret"


class Settings(BaseSettings):
    """CertPilot API 런타임 설정.

    비밀 값은 절대 코드나 기본값에 넣지 않는다. `.env.example`을 참고해
    로컬 `.env`를 만들어 쓴다.
    """

    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", _API_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 데이터베이스 / 캐시
    database_url: str = "postgresql+psycopg://certpilot:certpilot@localhost:5432/certpilot"
    redis_url: str = "redis://localhost:6379/0"

    # 오브젝트 스토리지 (로컬은 MinIO, 운영은 S3 호환 스토리지)
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "certpilot"
    s3_secret_key: str = "certpilot"
    s3_bucket: str = "certpilot"
    # SigV4 서명 리전. MinIO 는 무엇이든 통과하지만 실제 S3 호환 스토리지는 버킷
    # 리전과 일치해야 한다(예: ap-northeast-2, 오라클은 ap-seoul-1 등).
    s3_region: str = "us-east-1"

    # LLM (없어도 부팅은 되어야 한다). 기본 프로바이더는 OpenAI 다.
    # llm_provider: auto(키 있는 것 자동: openai → anthropic → fake) | openai | anthropic | fake
    llm_provider: str = "auto"
    openai_api_key: str | None = None
    # 2026-08 기준 균형값. gpt-5.6-luna(저가)·gpt-5.5(고성능) 등으로 교체 가능.
    openai_model: str = "gpt-5.6"
    anthropic_api_key: str | None = None

    # 임베딩: auto(OPENAI_API_KEY 있으면 openai, 없으면 hashing) | openai | hashing.
    # 프로바이더를 바꾸면 기존 청크 벡터와 비교 불가 — 문서 재인제스트(make demo)가 필요하다.
    embedding_provider: str = "auto"
    openai_embedding_model: str = "text-embedding-3-small"  # 기본 1536차원(Vector(1536) 일치)

    # 세션 쿠키 서명 키. 운영에서는 반드시 `.env`/시크릿 매니저의 값으로 덮어쓴다.
    # 여기 기본값은 로컬·테스트 전용 더미이며 실제 비밀이 아니다.
    session_secret: str = DEV_SESSION_SECRET
    session_cookie_name: str = "certpilot_session"
    session_max_age_seconds: int = 60 * 60 * 12
    # 로컬 개발은 http 라 False. 운영 배포에서는 True 로 덮어쓴다.
    session_cookie_secure: bool = False

    # 커넥터 자격증명 암호화 키(Fernet, base64 32바이트). 비워 두면 개발용으로
    # `session_secret` 에서 파생한 키를 쓰고 경고 로그를 남긴다(PRD §10).
    connector_encryption_key: str | None = None

    # 브라우저 프런트 출처(CORS 허용 대상). 쉼표로 여러 개. 운영 배포 시 실제 도메인으로 덮어쓴다.
    web_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # 정규식 허용(개발 편의): 기본값은 사설망(LAN) 출처를 허용해 같은 공유기의 다른
    # 기기에서 http://192.168.x.x:3000 으로 접속할 수 있게 한다. 운영에서는 빈 값으로
    # 꺼서 web_origins 명시 목록만 쓰는 것을 권장한다.
    web_origin_regex: str | None = (
        r"^https?://(localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(:\d{1,5})?$"
    )

    @field_validator(
        "anthropic_api_key", "openai_api_key", "connector_encryption_key", "web_origin_regex",
        mode="after",
    )
    @classmethod
    def _empty_to_none(cls, value: str | None) -> str | None:
        """`.env` 에 `ANTHROPIC_API_KEY=` 로 비워 둔 경우도 미설정으로 본다."""
        if value is None or not value.strip():
            return None
        return value

    @field_validator("session_secret", mode="after")
    @classmethod
    def _fallback_session_secret(cls, value: str) -> str:
        """`.env` 에 `SESSION_SECRET=` 로 비워 두면 개발용 기본값을 쓴다."""
        if not value.strip():
            return DEV_SESSION_SECRET
        return value


@lru_cache
def get_settings() -> Settings:
    """설정 싱글턴. 프로세스당 한 번만 읽는다."""
    return Settings()
