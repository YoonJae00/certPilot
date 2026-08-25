"""커넥터 자격증명 암호화 (PRD §10).

클라우드 자격증명·토큰은 **평문으로 저장하지 않는다.** 커넥터 설정에서 비밀에
해당하는 값들은 JSON 으로 묶어 Fernet 으로 암호화한 뒤 `connectors.config_json`
안에 암호문 문자열 하나로만 남는다.

키는 `CONNECTOR_ENCRYPTION_KEY`(base64 32바이트)를 쓴다. 운영에서는 KMS 또는
시크릿 매니저가 주입한다. 값이 없으면 개발 편의를 위해 `SESSION_SECRET` 에서
파생한 키를 쓰지만 경고 로그를 남긴다(운영에서는 반드시 설정한다).

이 모듈은 평문을 절대 로그로 남기지 않는다. 예외 메시지에도 넣지 않는다.
"""

import base64
import hashlib
import json
import logging
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# 개발용 파생 키의 도메인 분리 라벨. 세션 서명 키와 같은 값이 나오지 않게 한다.
_DERIVE_INFO = b"certpilot/connector-credentials/v1"


class CryptoError(RuntimeError):
    """암·복호화 실패. 원문·키를 메시지에 담지 않는다."""


def _derive_dev_key(secret: str) -> bytes:
    """개발 환경용 파생 키. `SESSION_SECRET` + 라벨을 sha256 해 Fernet 키로 만든다."""
    digest = hashlib.sha256(_DERIVE_INFO + secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache
def get_fernet() -> Fernet:
    """Fernet 인스턴스 싱글턴. 설정을 바꾸면 `get_fernet.cache_clear()` 를 부른다."""
    settings = get_settings()
    configured = settings.connector_encryption_key
    if configured:
        try:
            return Fernet(configured.encode("utf-8"))
        except (ValueError, TypeError) as error:
            # 키 값 자체는 절대 로그·예외에 넣지 않는다.
            raise CryptoError(
                "CONNECTOR_ENCRYPTION_KEY 형식이 올바르지 않다(base64 32바이트여야 한다)"
            ) from error

    logger.warning(
        "CONNECTOR_ENCRYPTION_KEY 가 없어 SESSION_SECRET 에서 파생한 개발용 키를 쓴다. "
        "운영 배포에서는 반드시 별도 키를 설정한다"
    )
    return Fernet(_derive_dev_key(settings.session_secret))


def encrypt_text(plaintext: str) -> str:
    """문자열을 암호화해 Fernet 토큰 문자열로 돌려준다."""
    return get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_text(token: str) -> str:
    """Fernet 토큰을 복호화한다. 키가 다르거나 손상되면 `CryptoError`."""
    try:
        return get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as error:
        raise CryptoError("커넥터 자격증명을 복호화할 수 없다(키가 바뀌었을 수 있다)") from error


def encrypt_json(payload: dict[str, Any]) -> str:
    """딕셔너리를 JSON 직렬화한 뒤 암호화한다."""
    return encrypt_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def decrypt_json(token: str) -> dict[str, Any]:
    """`encrypt_json` 으로 만든 토큰을 딕셔너리로 되돌린다."""
    raw = decrypt_text(token)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CryptoError("커넥터 자격증명 형식이 올바르지 않다") from error
    if not isinstance(data, dict):
        raise CryptoError("커넥터 자격증명 형식이 올바르지 않다")
    return data
