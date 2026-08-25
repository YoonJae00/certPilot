"""커넥터 설정(`connectors.config_json`) 저장 형식.

자격증명은 **암호문 하나로만** 저장한다(PRD §10). 화면에 보여 줄 값(연결 방식·리전·
마스킹된 ARN)만 평문으로 남겨서, 목록 조회가 복호화 없이 동작하게 한다.

저장 형식::

    {
      "version": 1,
      "auth_type": "role",
      "region": "ap-northeast-2",
      "role_arn_masked": "arn:aws:iam::********9012:role/CertPilotReadOnly",
      "account_id_masked": "********9012",
      "secret_ciphertext": "gAAAAAB..."
    }
"""

from typing import Any

from app.connectors.aws import (
    AUTH_TYPE_ROLE,
    AwsAuth,
    ConnectorError,
    mask_access_key_id,
    mask_arn,
    parse_auth,
)
from app.services.crypto import CryptoError, decrypt_json, encrypt_json

# 저장 형식 버전. 형식을 바꾸면 올리고 마이그레이션 코드를 붙인다.
CONFIG_VERSION = 1

# 암호문이 들어가는 키. 이 키 말고 어떤 자리에도 자격증명을 두지 않는다.
SECRET_FIELD = "secret_ciphertext"

# 평문으로 남겨도 되는 키(전부 마스킹됐거나 비밀이 아니다).
PUBLIC_FIELDS = (
    "version",
    "auth_type",
    "region",
    "role_arn_masked",
    "access_key_id_masked",
    "account_id_masked",
)


def build_stored_config(auth: AwsAuth, *, account_id_masked: str | None = None) -> dict[str, Any]:
    """`AwsAuth` 를 DB 에 저장할 `config_json` 으로 만든다."""
    secret: dict[str, Any] = {
        "auth_type": auth.auth_type,
        "region": auth.region,
        "role_arn": auth.role_arn,
        "external_id": auth.external_id,
        "access_key_id": auth.access_key_id,
        "secret_access_key": auth.secret_access_key,
    }

    stored: dict[str, Any] = {
        "version": CONFIG_VERSION,
        "auth_type": auth.auth_type,
        "region": auth.region,
        SECRET_FIELD: encrypt_json(secret),
    }
    if auth.auth_type == AUTH_TYPE_ROLE:
        stored["role_arn_masked"] = mask_arn(auth.role_arn)
    else:
        stored["access_key_id_masked"] = mask_access_key_id(auth.access_key_id)
    if account_id_masked:
        stored["account_id_masked"] = account_id_masked
    return stored


def load_auth(config: dict[str, Any]) -> AwsAuth:
    """저장된 `config_json` 에서 연결 정보를 복호화한다."""
    token = config.get(SECRET_FIELD)
    if not isinstance(token, str) or not token:
        raise ConnectorError("커넥터에 저장된 자격증명이 없다")
    try:
        secret = decrypt_json(token)
    except CryptoError as error:
        raise ConnectorError(str(error)) from error
    return parse_auth(secret)


def summarize_config(config: dict[str, Any]) -> dict[str, Any]:
    """API 응답용 요약. 암호문과 알 수 없는 키는 전부 버린다."""
    return {key: config.get(key) for key in PUBLIC_FIELDS if config.get(key) is not None}
