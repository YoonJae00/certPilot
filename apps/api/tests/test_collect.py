"""AWS 증적 수집 테스트 (PRD §7 F5 · §9).

AWS 는 moto 로 가짜 계정을 띄운다. moto 가 상태를 만들 수 없는 점검(루트 MFA,
액세스 키 나이)만 클라이언트 스텁으로 대신한다.

여기 나오는 액세스 키·비밀번호는 전부 가짜 문자열이다. 실제 자격증명을 넣지 않는다
(CLAUDE.md 절대 규칙 3).
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import boto3
import pytest
from moto import mock_aws
from sqlalchemy import select

from app.connectors.aws import (
    AwsAuth,
    ClientFactory,
    check_iam_key_age,
    check_iam_root_mfa,
    run_check,
)
from app.connectors.credentials import build_stored_config
from app.models import (
    Alert,
    AlertType,
    Connector,
    ConnectorStatus,
    ConnectorType,
    Evidence,
    EvidenceStatus,
)
from app.services.rules import evaluate_rules
from app.workers.collect import run_collect
from tests.conftest import make_org, make_project

REGION = "ap-northeast-2"

# 픽스처 전용 가짜 값.
FAKE_ACCESS_KEY_ID = "FIXTUREKEYID00000001"
FAKE_SECRET_ACCESS_KEY = "fixture-secret-not-a-real-aws-key-0001"
FAKE_CONSOLE_PASSWORD = "Fixture-Console-Pw-1234!"


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
        # AdministratorAccess 같은 AWS 관리형 정책은 이 값이 있어야 moto 가 로드한다.
        ("MOTO_IAM_LOAD_MANAGED_POLICIES", "true"),
    ):
        monkeypatch.setenv(name, value)


@pytest.fixture
def aws(aws_credentials):
    """moto 가짜 AWS 계정. 클라이언트 팩토리를 돌려준다."""
    with mock_aws():

        def factory(service: str) -> Any:
            return boto3.client(service, region_name=REGION)

        yield factory


def stub_factory(**clients: Any) -> ClientFactory:
    """서비스 이름으로 스텁 클라이언트를 돌려주는 팩토리."""

    def factory(service: str) -> Any:
        if service not in clients:
            raise AssertionError(f"스텁에 없는 서비스를 호출했다: {service}")
        return clients[service]

    return factory


def make_aws_connector(db, project_id) -> Connector:
    """액세스 키 방식 AWS 커넥터를 만든다(자격증명은 가짜)."""
    auth = AwsAuth(
        auth_type="access_key",
        region=REGION,
        access_key_id=FAKE_ACCESS_KEY_ID,
        secret_access_key=FAKE_SECRET_ACCESS_KEY,
    )
    connector = Connector(
        project_id=project_id,
        type=ConnectorType.AWS,
        config_json=build_stored_config(auth, account_id_masked="********0000"),
        status=ConnectorStatus.CONNECTED,
    )
    db.add(connector)
    db.commit()
    db.refresh(connector)
    return connector


# ---------------------------------------------------------------------------
# 점검 10개 (PRD §9 표)
# ---------------------------------------------------------------------------


def test_root_mfa_fail(aws):
    """루트 MFA 가 꺼져 있으면 fail. (moto 는 AccountMFAEnabled 를 항상 0 으로 준다)"""
    outcome = run_check("aws.iam.root_mfa", aws)
    assert outcome.status is EvidenceStatus.FAIL
    assert outcome.payload["account_mfa_enabled"] == 0


def test_root_mfa_pass_with_stub():
    """루트 MFA 가 켜져 있으면 pass. moto 로는 켤 수 없어 스텁을 쓴다."""
    iam = SimpleNamespace(get_account_summary=lambda: {"SummaryMap": {"AccountMFAEnabled": 1}})
    outcome = check_iam_root_mfa(stub_factory(iam=iam))
    assert outcome.status is EvidenceStatus.PASS


def test_user_mfa_pass(aws):
    """콘솔 사용자 전원에게 MFA 가 있으면 pass."""
    iam = aws("iam")
    iam.create_user(UserName="console-user")
    iam.create_login_profile(UserName="console-user", Password=FAKE_CONSOLE_PASSWORD)
    iam.enable_mfa_device(
        UserName="console-user",
        SerialNumber="arn:aws:iam::123456789012:mfa/console-user",
        AuthenticationCode1="111111",
        AuthenticationCode2="222222",
    )
    # 콘솔 로그인이 없는 사용자는 분모에 넣지 않는다.
    iam.create_user(UserName="api-only-user")

    outcome = run_check("aws.iam.user_mfa", aws)
    assert outcome.status is EvidenceStatus.PASS
    assert outcome.payload == {
        "users": 2,
        "console_users": 1,
        "mfa_enabled": 1,
        "missing": [],
    }


def test_user_mfa_fail(aws):
    """MFA 없는 콘솔 사용자가 있으면 fail 이고 목록이 남는다."""
    iam = aws("iam")
    iam.create_user(UserName="no-mfa-user")
    iam.create_login_profile(UserName="no-mfa-user", Password=FAKE_CONSOLE_PASSWORD)

    outcome = run_check("aws.iam.user_mfa", aws)
    assert outcome.status is EvidenceStatus.FAIL
    assert outcome.payload["missing"] == ["no-mfa-user"]


def test_password_policy_pass(aws):
    """길이·복잡도·만료를 모두 만족하면 pass."""
    aws("iam").update_account_password_policy(
        MinimumPasswordLength=12,
        RequireSymbols=True,
        RequireNumbers=True,
        RequireUppercaseCharacters=True,
        RequireLowercaseCharacters=True,
        MaxPasswordAge=90,
    )
    outcome = run_check("aws.iam.password_policy", aws)
    assert outcome.status is EvidenceStatus.PASS
    assert outcome.payload["reasons"] == []


def test_password_policy_fail_when_weak(aws):
    """길이·복잡도·만료 중 하나라도 빠지면 fail 이고 사유가 남는다."""
    aws("iam").update_account_password_policy(
        MinimumPasswordLength=6,
        RequireSymbols=False,
        RequireNumbers=False,
        RequireUppercaseCharacters=False,
        RequireLowercaseCharacters=False,
    )
    outcome = run_check("aws.iam.password_policy", aws)
    assert outcome.status is EvidenceStatus.FAIL
    assert len(outcome.payload["reasons"]) == 3


def test_password_policy_fail_when_missing(aws):
    """정책 자체가 없으면 fail."""
    outcome = run_check("aws.iam.password_policy", aws)
    assert outcome.status is EvidenceStatus.FAIL
    assert outcome.payload["policy_exists"] is False


def test_key_age_pass(aws):
    """방금 만든 키만 있으면 pass."""
    iam = aws("iam")
    iam.create_user(UserName="key-user")
    iam.create_access_key(UserName="key-user")

    outcome = run_check("aws.iam.key_age", aws)
    assert outcome.status is EvidenceStatus.PASS
    assert outcome.payload["active_keys"] == 1
    assert outcome.payload["expired_keys"] == []


def test_key_age_fail_with_stub():
    """90일이 지난 활성 키가 있으면 fail. moto 로는 생성일을 되돌릴 수 없어 스텁을 쓴다."""
    old = datetime.now(UTC) - timedelta(days=200)
    iam = SimpleNamespace(
        list_users=lambda **_: {"Users": [{"UserName": "old-key-user"}], "IsTruncated": False},
        list_access_keys=lambda **_: {
            "AccessKeyMetadata": [
                {
                    "UserName": "old-key-user",
                    "AccessKeyId": "FIXTUREKEYID00000002",
                    "Status": "Active",
                    "CreateDate": old,
                }
            ],
            "IsTruncated": False,
        },
    )
    outcome = check_iam_key_age(stub_factory(iam=iam))
    assert outcome.status is EvidenceStatus.FAIL
    assert outcome.payload["expired_keys"][0]["age_days"] == 200
    # 키 ID 는 마스킹돼 남는다.
    assert outcome.payload["expired_keys"][0]["access_key_id"] == "FIXT************0002"


def test_admin_users_pass(aws):
    """AdministratorAccess 를 직접 받은 사용자가 없으면 pass."""
    aws("iam").create_user(UserName="plain-user")
    outcome = run_check("aws.iam.admin_users", aws)
    assert outcome.status is EvidenceStatus.PASS
    assert outcome.payload["admin_user_count"] == 0


def test_admin_users_warn(aws):
    """1~3명이면 warn(목록 제공)."""
    iam = aws("iam")
    iam.create_user(UserName="admin-1")
    iam.attach_user_policy(
        UserName="admin-1", PolicyArn="arn:aws:iam::aws:policy/AdministratorAccess"
    )
    outcome = run_check("aws.iam.admin_users", aws)
    assert outcome.status is EvidenceStatus.WARN
    assert outcome.payload["admin_users"] == ["admin-1"]


def test_admin_users_fail_over_limit(aws):
    """3명을 초과하면 fail."""
    iam = aws("iam")
    for index in range(4):
        name = f"admin-{index}"
        iam.create_user(UserName=name)
        iam.attach_user_policy(
            UserName=name, PolicyArn="arn:aws:iam::aws:policy/AdministratorAccess"
        )
    outcome = run_check("aws.iam.admin_users", aws)
    assert outcome.status is EvidenceStatus.FAIL
    assert outcome.payload["admin_user_count"] == 4


def _create_trail(aws, *, multi_region: bool, validation: bool) -> None:
    """CloudTrail 트레일을 만든다(버킷이 먼저 있어야 한다)."""
    aws("s3").create_bucket(
        Bucket="certpilot-trail-logs",
        CreateBucketConfiguration={"LocationConstraint": REGION},
    )
    aws("cloudtrail").create_trail(
        Name="certpilot-trail",
        S3BucketName="certpilot-trail-logs",
        IsMultiRegionTrail=multi_region,
        EnableLogFileValidation=validation,
    )


def test_cloudtrail_pass(aws):
    """전 리전 + 무결성 검증 트레일이 있으면 pass."""
    _create_trail(aws, multi_region=True, validation=True)
    outcome = run_check("aws.cloudtrail.enabled", aws)
    assert outcome.status is EvidenceStatus.PASS
    assert outcome.payload["compliant_trails"] == ["certpilot-trail"]


def test_cloudtrail_fail_without_validation(aws):
    """무결성 검증이 꺼져 있으면 fail."""
    _create_trail(aws, multi_region=True, validation=False)
    outcome = run_check("aws.cloudtrail.enabled", aws)
    assert outcome.status is EvidenceStatus.FAIL
    assert outcome.payload["compliant_trails"] == []


def test_cloudtrail_fail_when_none(aws):
    """트레일이 하나도 없으면 fail."""
    outcome = run_check("aws.cloudtrail.enabled", aws)
    assert outcome.status is EvidenceStatus.FAIL
    assert outcome.payload["trails"] == 0


def _create_bucket(aws, name: str) -> None:
    """테스트용 버킷 1개."""
    aws("s3").create_bucket(
        Bucket=name, CreateBucketConfiguration={"LocationConstraint": REGION}
    )


def test_s3_public_block_pass(aws):
    """모든 버킷에 차단 4개 옵션이 켜져 있으면 pass."""
    _create_bucket(aws, "certpilot-blocked")
    aws("s3").put_public_access_block(
        Bucket="certpilot-blocked",
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    outcome = run_check("aws.s3.public_block", aws)
    assert outcome.status is EvidenceStatus.PASS
    assert outcome.payload == {
        "buckets": 1,
        "blocked": 1,
        "unblocked": [],
        "errors": [],
    }


def test_s3_public_block_fail(aws):
    """차단 설정이 없는 버킷이 있으면 fail 이고 버킷 이름이 남는다."""
    _create_bucket(aws, "certpilot-open")
    outcome = run_check("aws.s3.public_block", aws)
    assert outcome.status is EvidenceStatus.FAIL
    assert outcome.payload["unblocked"] == ["certpilot-open"]


def test_s3_encryption_pass(aws):
    """기본 암호화가 설정돼 있으면 pass."""
    _create_bucket(aws, "certpilot-encrypted")
    aws("s3").put_bucket_encryption(
        Bucket="certpilot-encrypted",
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )
    outcome = run_check("aws.s3.encryption", aws)
    assert outcome.status is EvidenceStatus.PASS
    assert outcome.payload["encrypted"] == 1


def test_s3_encryption_fail(aws):
    """기본 암호화가 없는 버킷이 있으면 fail."""
    _create_bucket(aws, "certpilot-plain")
    outcome = run_check("aws.s3.encryption", aws)
    assert outcome.status is EvidenceStatus.FAIL
    assert outcome.payload["unencrypted"] == ["certpilot-plain"]


def _create_db(aws, identifier: str, *, encrypted: bool, retention: int) -> None:
    """테스트용 RDS 인스턴스 1개."""
    aws("rds").create_db_instance(
        DBInstanceIdentifier=identifier,
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
        AllocatedStorage=20,
        StorageEncrypted=encrypted,
        BackupRetentionPeriod=retention,
    )


def test_rds_encryption_pass(aws):
    """저장 암호화 + 백업 7일 이상이면 pass."""
    _create_db(aws, "certpilot-db-ok", encrypted=True, retention=7)
    outcome = run_check("aws.rds.encryption", aws)
    assert outcome.status is EvidenceStatus.PASS
    assert outcome.payload["compliant"] == 1


def test_rds_encryption_fail(aws):
    """암호화가 꺼져 있거나 백업이 7일 미만이면 fail."""
    _create_db(aws, "certpilot-db-bad", encrypted=False, retention=1)
    outcome = run_check("aws.rds.encryption", aws)
    assert outcome.status is EvidenceStatus.FAIL
    violation = outcome.payload["violations"][0]
    assert violation["db_instance"] == "certpilot-db-bad"
    assert violation["storage_encrypted"] is False
    assert violation["backup_retention_days"] == 1


def test_open_sg_pass(aws):
    """전체 개방 규칙이 없으면 pass(기본 보안 그룹만 있는 상태)."""
    outcome = run_check("aws.ec2.open_sg", aws)
    assert outcome.status is EvidenceStatus.PASS
    assert outcome.payload["open_rules"] == []


def test_open_sg_fail(aws):
    """0.0.0.0/0 에서 22 번 포트가 열려 있으면 fail."""
    ec2 = aws("ec2")
    vpc_id = ec2.describe_vpcs()["Vpcs"][0]["VpcId"]
    group_id = ec2.create_security_group(
        GroupName="certpilot-open-sg", Description="테스트용", VpcId=vpc_id
    )["GroupId"]
    ec2.authorize_security_group_ingress(
        GroupId=group_id,
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            }
        ],
    )

    outcome = run_check("aws.ec2.open_sg", aws)
    assert outcome.status is EvidenceStatus.FAIL
    assert [rule["port"] for rule in outcome.payload["open_rules"]] == [22]


def test_check_failure_becomes_unknown_and_continues():
    """점검 하나가 실패해도 예외를 던지지 않고 unknown 으로 남긴다."""

    def explode() -> dict[str, Any]:
        raise RuntimeError("의도적 실패")

    iam = SimpleNamespace(get_account_summary=explode)
    outcome = run_check("aws.iam.root_mfa", stub_factory(iam=iam))
    assert outcome.status is EvidenceStatus.UNKNOWN
    assert outcome.payload["error"] == "RuntimeError"


# ---------------------------------------------------------------------------
# 수집 잡: 스냅샷 · diff · drift 알림
# ---------------------------------------------------------------------------


def test_collect_writes_one_snapshot(db, aws):
    """수집 1회에 점검 10개가 같은 snapshot_id 로 저장된다."""
    org = make_org(db, "수집조직")
    project = make_project(db, org.id, "수집프로젝트")
    connector = make_aws_connector(db, project.id)

    result = run_collect(connector.id, db=db)

    assert result.evidence_count == 10
    assert result.alert_count == 0

    rows = list(
        db.execute(select(Evidence).where(Evidence.connector_id == connector.id)).scalars()
    )
    assert len(rows) == 10
    assert {row.snapshot_id for row in rows} == {result.snapshot_id}
    assert all(row.criterion_codes for row in rows)

    db.refresh(connector)
    assert connector.last_collected_at is not None
    assert connector.status is ConnectorStatus.CONNECTED


def test_collect_detects_drift(db, aws):
    """스냅샷 2회 사이에 설정이 바뀌면 drift 알림 1건이 생긴다."""
    org = make_org(db, "변경조직")
    project = make_project(db, org.id, "변경프로젝트")
    connector = make_aws_connector(db, project.id)

    _create_bucket(aws, "certpilot-drift")
    aws("s3").put_public_access_block(
        Bucket="certpilot-drift",
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )

    first = run_collect(connector.id, db=db)
    assert first.alert_count == 0

    # 퍼블릭 액세스 차단을 해제한다(테스트 준비용 변경).
    aws("s3").delete_public_access_block(Bucket="certpilot-drift")

    second = run_collect(connector.id, db=db)
    assert second.snapshot_id != first.snapshot_id
    assert second.alert_count == 1

    alerts = list(db.execute(select(Alert).where(Alert.project_id == project.id)).scalars())
    assert len(alerts) == 1
    assert alerts[0].type is AlertType.DRIFT
    assert "S3 퍼블릭 액세스 차단" in alerts[0].message
    assert "판정이 충족 → 미충족으로 바뀌었다" in alerts[0].message
    assert "차단 미적용 버킷 0개 → 1개" in alerts[0].message
    # 알림은 이번 스냅샷의 증적을 가리킨다.
    assert alerts[0].evidence_id is not None
    evidence = db.execute(
        select(Evidence).where(Evidence.id == alerts[0].evidence_id)
    ).scalar_one()
    assert evidence.check_id == "aws.s3.public_block"
    assert evidence.snapshot_id == second.snapshot_id


def test_collect_without_change_makes_no_alert(db, aws):
    """설정이 그대로면 알림을 만들지 않는다."""
    org = make_org(db, "무변경조직")
    project = make_project(db, org.id, "무변경프로젝트")
    connector = make_aws_connector(db, project.id)

    run_collect(connector.id, db=db)
    second = run_collect(connector.id, db=db)

    assert second.alert_count == 0
    alerts = list(db.execute(select(Alert).where(Alert.project_id == project.id)).scalars())
    assert alerts == []


def test_collect_records_audit_log(db, aws):
    """수집은 감사 로그에 남는다(PRD §10)."""
    from app.models import AuditLog

    org = make_org(db, "감사조직")
    project = make_project(db, org.id, "감사프로젝트")
    connector = make_aws_connector(db, project.id)

    run_collect(connector.id, db=db)

    log = db.execute(
        select(AuditLog).where(AuditLog.action == "connector.collect")
    ).scalar_one()
    assert log.org_id == org.id
    assert log.target == str(connector.id)
    assert log.meta_json["evidence_count"] == 10


def test_collected_evidence_feeds_rule_engine(db, aws):
    """수집한 증적을 `evaluate_rules()` 가 항목 판정으로 읽는다."""
    org = make_org(db, "규칙조직")
    project = make_project(db, org.id, "규칙프로젝트")
    connector = make_aws_connector(db, project.id)

    run_collect(connector.id, db=db)

    # 트레일이 없는 계정이므로 2.9.4(로그 및 접속기록 관리)는 fail 이어야 한다.
    result = evaluate_rules(db, project.id, "2.9.4")
    assert result.has_evidence
    assert result.verdict == "fail"
    assert any(item.check_id == "aws.cloudtrail.enabled" for item in result.failed)
