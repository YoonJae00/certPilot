"""증적 커넥터 API·보안 테스트 (PRD §7 F5 · §9 · §10).

핵심은 세 가지다.

1. 매핑 파일(`data/rules/aws_rules.yaml`)의 항목 코드가 `criteria.json` 에 실존한다.
2. 자격증명이 DB 평문·API 응답·로그 어디에도 남지 않는다.
3. org 스코프를 벗어난 접근은 404, 권한 없는 역할은 403 이다.

여기 나오는 액세스 키·외부 ID 는 전부 가짜 문자열이다.
"""

import ast
import json
import logging
import re
from pathlib import Path

import pytest
from moto import mock_aws
from sqlalchemy import select, text

from app.connectors.aws import CHECK_FUNCTIONS, mask_access_key_id, mask_arn
from app.connectors.credentials import SECRET_FIELD, load_auth
from app.connectors.mapping import load_check_mappings
from app.models import Connector, ConnectorStatus, Evidence
from app.services.criteria_loader import load_criteria_file
from app.services.crypto import CryptoError, decrypt_text, encrypt_text
from tests.conftest import login

REGION = "ap-northeast-2"
API_ROOT = Path(__file__).resolve().parents[1]
CONNECTORS_PACKAGE = API_ROOT / "app" / "connectors"

# 픽스처 전용 가짜 값. 실제 자격증명이 아니다.
FAKE_ROLE_ARN = "arn:aws:iam::123456789012:role/CertPilotReadOnly"
FAKE_EXTERNAL_ID = "fixture-external-id-0123456789"
FAKE_ACCESS_KEY_ID = "FIXTUREKEYID00000001"
FAKE_SECRET_ACCESS_KEY = "fixture-secret-not-a-real-aws-key-0001"

# 클라우드 쓰기 API 로 볼 수 있는 메서드 이름 패턴(CLAUDE.md 절대 규칙 4).
WRITE_METHOD_PATTERN = re.compile(
    r"^(create|put|delete|update|modify|attach|detach|enable|disable|"
    r"remove|add|start|stop|terminate|reboot|restore|revoke|authorize)_"
)


@pytest.fixture
def aws_credentials(monkeypatch):
    """moto 가 가로챌 수 있도록 가짜 환경 자격증명을 심는다."""
    for name, value in (
        ("AWS_ACCESS_KEY_ID", "testing"),
        ("AWS_SECRET_ACCESS_KEY", "testing"),
        ("AWS_SECURITY_TOKEN", "testing"),
        ("AWS_SESSION_TOKEN", "testing"),
        ("AWS_DEFAULT_REGION", REGION),
        ("AWS_REGION", REGION),
    ):
        monkeypatch.setenv(name, value)


@pytest.fixture
def aws(aws_credentials):
    """moto 가짜 AWS 계정."""
    with mock_aws():
        yield


@pytest.fixture(autouse=True)
def _no_celery(monkeypatch):
    """테스트에서는 항상 동기 수집 폴백을 쓴다(브로커에 붙지 않는다)."""
    monkeypatch.setattr("app.api.connectors.enqueue_collect", lambda connector_id: False)


def role_payload() -> dict[str, object]:
    """역할(AssumeRole) 방식 생성 요청 본문."""
    return {
        "type": "aws",
        "config": {
            "auth_type": "role",
            "region": REGION,
            "role_arn": FAKE_ROLE_ARN,
            "external_id": FAKE_EXTERNAL_ID,
        },
    }


def access_key_payload() -> dict[str, object]:
    """액세스 키 방식 생성 요청 본문."""
    return {
        "type": "aws",
        "config": {
            "auth_type": "access_key",
            "region": REGION,
            "access_key_id": FAKE_ACCESS_KEY_ID,
            "secret_access_key": FAKE_SECRET_ACCESS_KEY,
        },
    }


# ---------------------------------------------------------------------------
# 매핑 파일 검증 (§9 개정 대비)
# ---------------------------------------------------------------------------


def test_mapping_covers_ten_checks():
    """매핑 파일에 PRD §9 의 점검 10개가 있고, 코드에 구현이 모두 있다."""
    mappings = load_check_mappings()
    assert len(mappings) == 10
    assert set(mappings) == set(CHECK_FUNCTIONS)


def test_mapping_criterion_codes_exist_in_criteria_json():
    """매핑의 모든 항목 코드가 `criteria.json` 에 실존한다(안내서 개정 대비)."""
    _, items = load_criteria_file()
    known = {str(item["code"]) for item in items}

    missing: list[str] = []
    for mapping in load_check_mappings().values():
        assert mapping.criterion_codes, f"{mapping.check_id} 에 매핑된 항목이 없다"
        missing.extend(
            f"{mapping.check_id} → {code}"
            for code in mapping.criterion_codes
            if code not in known
        )
    assert missing == [], f"criteria.json 에 없는 항목 코드가 있다: {missing}"


def test_connector_package_has_no_write_api_calls():
    """커넥터 패키지에 클라우드 쓰기 API 호출이 없다(CLAUDE.md 절대 규칙 4)."""
    offenders: list[str] = []
    for path in sorted(CONNECTORS_PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders.extend(
            f"{path.name}:{node.lineno} {node.attr}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and WRITE_METHOD_PATTERN.match(node.attr)
        )
    assert offenders == [], f"쓰기 API 로 보이는 호출이 있다: {offenders}"


# ---------------------------------------------------------------------------
# 암호화
# ---------------------------------------------------------------------------


def test_crypto_roundtrip():
    """암호화한 값은 원문과 다르고 복호화하면 되돌아온다."""
    token = encrypt_text(FAKE_SECRET_ACCESS_KEY)
    assert FAKE_SECRET_ACCESS_KEY not in token
    assert decrypt_text(token) == FAKE_SECRET_ACCESS_KEY


def test_crypto_rejects_broken_token():
    """손상된 토큰은 조용히 넘어가지 않고 예외가 된다."""
    with pytest.raises(CryptoError):
        decrypt_text("not-a-valid-fernet-token")


def test_mask_helpers():
    """계정 ID·액세스 키 ID 마스킹."""
    assert mask_arn(FAKE_ROLE_ARN) == "arn:aws:iam::********9012:role/CertPilotReadOnly"
    assert mask_access_key_id(FAKE_ACCESS_KEY_ID) == "FIXT************0001"


# ---------------------------------------------------------------------------
# 생성 · 목록
# ---------------------------------------------------------------------------


def test_create_connector_with_role(client, db, tenants, aws):
    """역할 방식으로 만들면 연결 테스트를 거쳐 connected 가 된다."""
    login(client, "admin-a@example.com")
    project = tenants["project_a"]

    response = client.post(f"/projects/{project.id}/connectors", json=role_payload())
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["status"] == "connected"
    assert body["config"]["auth_type"] == "role"
    assert body["config"]["region"] == REGION
    assert body["config"]["role_arn_masked"] == mask_arn(FAKE_ROLE_ARN)
    # 계정 ID 는 마스킹돼서만 나간다.
    assert body["config"]["account_id_masked"].endswith("0000") or body["config"][
        "account_id_masked"
    ].startswith("*")

    connector = db.execute(select(Connector)).scalar_one()
    auth = load_auth(dict(connector.config_json))
    assert auth.role_arn == FAKE_ROLE_ARN
    assert auth.external_id == FAKE_EXTERNAL_ID


def test_create_connector_with_access_key(client, db, tenants, aws):
    """액세스 키 방식도 만들 수 있고 키 ID 는 마스킹돼 나간다."""
    login(client, "admin-a@example.com")
    project = tenants["project_a"]

    response = client.post(f"/projects/{project.id}/connectors", json=access_key_payload())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["config"]["access_key_id_masked"] == mask_access_key_id(FAKE_ACCESS_KEY_ID)
    assert body["status"] == "connected"


def test_create_connector_requires_role_fields(client, tenants, aws):
    """역할 방식인데 external_id 가 없으면 422."""
    login(client, "admin-a@example.com")
    project = tenants["project_a"]

    response = client.post(
        f"/projects/{project.id}/connectors",
        json={
            "type": "aws",
            "config": {"auth_type": "role", "region": REGION, "role_arn": FAKE_ROLE_ARN},
        },
    )
    assert response.status_code == 422


def test_create_connector_marks_error_when_connection_fails(client, db, tenants, monkeypatch):
    """연결 테스트가 실패해도 커넥터는 만들되 status=error 로 남긴다."""
    from app.connectors.aws import ConnectorError

    def boom(_clients):
        raise ConnectorError("AWS 연결 확인에 실패했다(AccessDenied)")

    monkeypatch.setattr("app.api.connectors.test_connection", boom)

    login(client, "admin-a@example.com")
    project = tenants["project_a"]
    response = client.post(f"/projects/{project.id}/connectors", json=access_key_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "error"
    assert "AccessDenied" in body["error_reason"]

    connector = db.execute(select(Connector)).scalar_one()
    assert connector.status is ConnectorStatus.ERROR


def test_create_connector_rejects_github(client, tenants, aws):
    """GitHub 커넥터는 아직 지원하지 않는다(Task 7b)."""
    login(client, "admin-a@example.com")
    project = tenants["project_a"]
    payload = access_key_payload() | {"type": "github"}
    response = client.post(f"/projects/{project.id}/connectors", json=payload)
    assert response.status_code == 400


def test_list_connectors_masks_config(client, tenants, aws):
    """목록에는 마스킹 요약만 나가고 암호문·자격증명은 나가지 않는다."""
    login(client, "admin-a@example.com")
    project = tenants["project_a"]
    client.post(f"/projects/{project.id}/connectors", json=role_payload())

    response = client.get(f"/projects/{project.id}/connectors")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert set(body[0]["config"]) == {
        "auth_type",
        "region",
        "role_arn_masked",
        "access_key_id_masked",
        "account_id_masked",
    }
    assert SECRET_FIELD not in response.text


# ---------------------------------------------------------------------------
# 자격증명 비노출 (PRD §10)
# ---------------------------------------------------------------------------


def test_credentials_never_leak(client, db, tenants, aws, caplog):
    """자격증명이 DB 평문·API 응답·로그 어디에도 남지 않는다."""
    caplog.set_level(logging.DEBUG)
    login(client, "admin-a@example.com")
    project = tenants["project_a"]

    payload = role_payload()
    payload["config"] = dict(payload["config"]) | {
        "access_key_id": FAKE_ACCESS_KEY_ID,
        "secret_access_key": FAKE_SECRET_ACCESS_KEY,
    }
    create = client.post(f"/projects/{project.id}/connectors", json=payload)
    assert create.status_code == 201
    connector_id = create.json()["id"]

    collect = client.post(f"/projects/{project.id}/connectors/{connector_id}/collect")
    assert collect.status_code == 202
    listing = client.get(f"/projects/{project.id}/connectors")
    evidence = client.get(f"/projects/{project.id}/connectors/{connector_id}/evidence")

    secrets = (FAKE_SECRET_ACCESS_KEY, FAKE_EXTERNAL_ID)

    # (a) DB 에 저장된 config_json 원문
    raw_config = db.execute(text("SELECT config_json::text FROM connectors")).scalar_one()
    for secret in secrets:
        assert secret not in raw_config

    # 증적 payload 에도 자격증명이 없다.
    raw_evidence = db.execute(text("SELECT payload_json::text FROM evidence")).all()
    for (row,) in raw_evidence:
        for secret in secrets:
            assert secret not in row

    # (b) API 응답 전체
    for response in (create, collect, listing, evidence):
        for secret in secrets:
            assert secret not in response.text

    # 감사 로그 meta 에도 없다.
    raw_audit = db.execute(text("SELECT meta_json::text FROM audit_logs")).all()
    for (row,) in raw_audit:
        for secret in secrets:
            assert secret not in row

    # (c) 로그 전체
    log_text = "\n".join(record.getMessage() for record in caplog.records) + caplog.text
    for secret in secrets:
        assert secret not in log_text


def test_stored_config_keeps_only_ciphertext(client, db, tenants, aws):
    """`config_json` 에서 자격증명은 암호문 필드 하나로만 존재한다."""
    login(client, "admin-a@example.com")
    project = tenants["project_a"]
    client.post(f"/projects/{project.id}/connectors", json=role_payload())

    connector = db.execute(select(Connector)).scalar_one()
    config = dict(connector.config_json)
    assert SECRET_FIELD in config
    # 암호문을 뺀 나머지에는 어떤 자격증명도 없다.
    public_only = json.dumps({k: v for k, v in config.items() if k != SECRET_FIELD})
    assert FAKE_EXTERNAL_ID not in public_only
    assert "123456789012" not in public_only


# ---------------------------------------------------------------------------
# 권한 · 테넌트 격리
# ---------------------------------------------------------------------------


def test_reviewer_cannot_create_connector(client, tenants):
    """심사원은 커넥터를 만들 수 없다(403)."""
    login(client, "reviewer@example.com")
    project = tenants["project_a"]
    response = client.post(f"/projects/{project.id}/connectors", json=access_key_payload())
    assert response.status_code == 403


def test_reviewer_cannot_list_connectors(client, tenants):
    """심사원은 조직 스코프 조회도 막힌다(403)."""
    login(client, "reviewer@example.com")
    project = tenants["project_a"]
    assert client.get(f"/projects/{project.id}/connectors").status_code == 403


def test_org_member_cannot_create_connector(client, tenants):
    """일반 구성원은 커넥터를 만들 수 없다(403)."""
    login(client, "member-a@example.com")
    project = tenants["project_a"]
    response = client.post(f"/projects/{project.id}/connectors", json=access_key_payload())
    assert response.status_code == 403


def test_cross_tenant_project_is_404(client, tenants, aws):
    """다른 조직 프로젝트의 커넥터는 존재 여부도 흘리지 않는다(404)."""
    login(client, "admin-a@example.com")
    project_a = tenants["project_a"]
    created = client.post(f"/projects/{project_a.id}/connectors", json=role_payload())
    connector_id = created.json()["id"]

    login(client, "admin-b@example.com")
    project_b = tenants["project_b"]

    assert client.get(f"/projects/{project_a.id}/connectors").status_code == 404
    # 자기 프로젝트 아래에서 남의 커넥터 ID 를 불러도 404 다.
    assert (
        client.get(f"/projects/{project_b.id}/connectors/{connector_id}/evidence").status_code
        == 404
    )
    assert (
        client.post(f"/projects/{project_a.id}/connectors/{connector_id}/collect").status_code
        == 404
    )


def test_anonymous_is_401(client, tenants):
    """로그인하지 않으면 401."""
    project = tenants["project_a"]
    assert client.get(f"/projects/{project.id}/connectors").status_code == 401


# ---------------------------------------------------------------------------
# 수집 · 증적 조회
# ---------------------------------------------------------------------------


def test_collect_endpoint_runs_synchronously(client, db, tenants, aws):
    """워커가 없으면 요청 스레드에서 수집을 끝내고 202 로 결과를 돌려준다."""
    login(client, "admin-a@example.com")
    project = tenants["project_a"]
    connector_id = client.post(
        f"/projects/{project.id}/connectors", json=role_payload()
    ).json()["id"]

    response = client.post(f"/projects/{project.id}/connectors/{connector_id}/collect")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["state"] == "done"
    assert body["evidence_count"] == 10
    assert body["snapshot_id"]
    assert sum(body["status_counts"].values()) == 10

    rows = list(db.execute(select(Evidence)).scalars())
    assert len(rows) == 10


def test_list_evidence_filters(client, tenants, aws):
    """증적 목록은 스냅샷·점검 ID 로 거를 수 있다."""
    login(client, "admin-a@example.com")
    project = tenants["project_a"]
    connector_id = client.post(
        f"/projects/{project.id}/connectors", json=role_payload()
    ).json()["id"]
    snapshot_id = client.post(
        f"/projects/{project.id}/connectors/{connector_id}/collect"
    ).json()["snapshot_id"]

    base = f"/projects/{project.id}/connectors/{connector_id}/evidence"
    assert len(client.get(base, params={"snapshot": snapshot_id}).json()) == 10
    assert client.get(base, params={"snapshot": "없는스냅샷"}).json() == []

    filtered = client.get(base, params={"check_id": "aws.s3.encryption"}).json()
    assert len(filtered) == 1
    assert filtered[0]["criterion_codes"] == ["2.7.1"]
    assert filtered[0]["title"] == "S3 버킷 기본 암호화"


def test_latest_evidence_includes_mapping(client, tenants, aws):
    """최신 증적은 check_id 별 1건씩, 항목 매핑과 함께 나온다."""
    login(client, "admin-a@example.com")
    project = tenants["project_a"]
    connector_id = client.post(
        f"/projects/{project.id}/connectors", json=role_payload()
    ).json()["id"]
    # 두 번 수집해도 최신 것만 나와야 한다.
    client.post(f"/projects/{project.id}/connectors/{connector_id}/collect")
    second = client.post(
        f"/projects/{project.id}/connectors/{connector_id}/collect"
    ).json()

    response = client.get(f"/projects/{project.id}/connectors/evidence/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_id"] == second["snapshot_id"]
    assert len(body["items"]) == 10
    assert {item["snapshot_id"] for item in body["items"]} == {second["snapshot_id"]}
    for item in body["items"]:
        assert item["criterion_codes"]
        assert item["title"]
        assert item["pass_condition"]


def test_latest_evidence_empty_when_never_collected(client, tenants):
    """수집 이력이 없으면 빈 목록이다."""
    login(client, "admin-a@example.com")
    project = tenants["project_a"]
    response = client.get(f"/projects/{project.id}/connectors/evidence/latest")
    assert response.status_code == 200
    assert response.json()["items"] == []
