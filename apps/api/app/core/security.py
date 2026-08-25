"""비밀번호 해시와 세션 토큰.

- 비밀번호는 bcrypt 해시로만 저장한다. 평문은 어디에도 남기지 않는다.
- 세션은 서버 상태 없이 itsdangerous 서명 쿠키(user_id)로 관리한다. 시제품이라
  세션 저장소를 두지 않지만, 서명·만료 검증은 반드시 거친다.
"""

import uuid

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import get_settings

# bcrypt 는 72바이트를 넘는 입력을 받지 않는다.
MAX_PASSWORD_BYTES = 72
_SALT_NAMESPACE = "certpilot.session"


class PasswordTooLongError(ValueError):
    """비밀번호가 bcrypt 한계(72바이트)를 넘은 경우."""


def hash_password(password: str) -> str:
    """평문 비밀번호를 bcrypt 해시 문자열로 바꾼다."""
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise PasswordTooLongError("비밀번호는 72바이트를 넘을 수 없다")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """평문 비밀번호가 해시와 일치하는지 확인한다."""
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        # 저장된 해시는 72바이트 이하 입력으로만 만들어지므로 일치할 수 없다.
        return False
    return bcrypt.checkpw(raw, password_hash.encode("utf-8"))


def _serializer() -> URLSafeTimedSerializer:
    """세션 쿠키 서명기. 설정의 `session_secret` 을 키로 쓴다."""
    settings = get_settings()
    return URLSafeTimedSerializer(settings.session_secret, salt=_SALT_NAMESPACE)


def create_session_token(user_id: uuid.UUID) -> str:
    """user_id 를 담은 서명 세션 토큰을 만든다."""
    return _serializer().dumps({"user_id": str(user_id)})


def read_session_token(token: str) -> uuid.UUID | None:
    """세션 토큰을 검증해 user_id 를 돌려준다. 위조·만료·형식 오류면 None."""
    settings = get_settings()
    try:
        payload = _serializer().loads(token, max_age=settings.session_max_age_seconds)
    except (SignatureExpired, BadSignature):
        # 위조·만료는 정상적인 인증 실패 경로다. 그 외 예외는 삼키지 않는다.
        return None
    if not isinstance(payload, dict):
        return None
    raw_user_id = payload.get("user_id")
    if not isinstance(raw_user_id, str):
        return None
    try:
        return uuid.UUID(raw_user_id)
    except ValueError:
        return None
