"""AWS 증적 커넥터 (읽기 전용, PRD §7 F5 · §9).

연결 방식은 두 가지다.

1. `role`       : 고객 계정의 `CertPilotReadOnly` 역할을 외부 ID 조건과 함께
                  AssumeRole 한다(운영 기본, `infra/cloudformation/` 템플릿 참고).
2. `access_key` : 액세스 키를 직접 쓴다(개발·데모 전용).

**호출하는 AWS API 는 Describe / List / Get 과 sts:AssumeRole ·
sts:GetCallerIdentity 뿐이다.** 쓰기 API 를 부르는 코드를 여기에 추가하지 않는다
(CLAUDE.md 절대 규칙 4).

점검 결과 payload 에는 판정 근거가 되는 요약 수치·목록만 넣는다. 자격증명·시크릿은
어떤 경우에도 payload·로그·예외 메시지에 넣지 않는다(PRD §10).
"""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.connectors.mapping import CheckMapping, load_check_mappings
from app.models import EvidenceStatus

logger = logging.getLogger(__name__)

# botocore 는 DEBUG 레벨에서 요청 본문·서명 헤더를 그대로 찍는다. AssumeRole 요청
# 본문에는 외부 ID 가 들어 있어, 앱 로그 레벨을 DEBUG 로 낮추는 순간 자격증명이
# 로그에 남는다(PRD §10 금지). 커넥터를 임포트하는 순간 그 경로를 막는다.
SILENCED_AWS_LOGGERS = ("boto3", "botocore", "urllib3")


def _mute_aws_wire_logs() -> None:
    """boto3·botocore 의 DEBUG 로그를 INFO 로 올린다(요청 본문 유출 차단)."""
    for name in SILENCED_AWS_LOGGERS:
        wire_logger = logging.getLogger(name)
        if wire_logger.level in (logging.NOTSET, logging.DEBUG):
            wire_logger.setLevel(logging.INFO)


_mute_aws_wire_logs()

# 서비스 이름을 받아 boto3 클라이언트를 돌려주는 팩토리. 테스트는 스텁을 넣는다.
ClientFactory = Callable[[str], Any]

AUTH_TYPE_ROLE = "role"
AUTH_TYPE_ACCESS_KEY = "access_key"
AUTH_TYPES = (AUTH_TYPE_ROLE, AUTH_TYPE_ACCESS_KEY)

DEFAULT_REGION = "ap-northeast-2"

# AssumeRole 세션 이름·유효 시간. 수집 잡 1회분보다 넉넉하면 된다.
ROLE_SESSION_NAME = "certpilot-collect"
ROLE_SESSION_SECONDS = 3600

# 액세스 키 사용 기간 임계값(일). PRD §9.
MAX_ACCESS_KEY_AGE_DAYS = 90

# 특수 권한(AdministratorAccess) 사용자 수 임계값. 0명 pass, 1~3명 warn, 초과 fail.
ADMIN_USER_WARN_LIMIT = 3
ADMIN_POLICY_NAME = "AdministratorAccess"

# 인터넷 전체 개방으로 보는 CIDR 과, 열려 있으면 안 되는 포트(PRD §9).
OPEN_CIDRS = frozenset({"0.0.0.0/0"})
OPEN_CIDRS_V6 = frozenset({"::/0"})
SENSITIVE_PORTS = (22, 3389, 3306, 5432)

# 퍼블릭 액세스 차단 4개 옵션.
PUBLIC_ACCESS_BLOCK_KEYS = (
    "BlockPublicAcls",
    "IgnorePublicAcls",
    "BlockPublicPolicy",
    "RestrictPublicBuckets",
)

# RDS 자동 백업 최소 보관 기간(일). PRD §9.
MIN_BACKUP_RETENTION_DAYS = 7

# 비밀번호 정책 최소 길이. PRD §9.
MIN_PASSWORD_LENGTH = 8

# "설정이 없다" 를 뜻하는 에러 코드. 접근 거부와 구분해야 판정이 정확해진다.
_NOT_CONFIGURED_CODES = frozenset(
    {
        "NoSuchEntity",
        "NoSuchEntityException",
        "NoSuchPublicAccessBlockConfiguration",
        "ServerSideEncryptionConfigurationNotFoundError",
    }
)

# 페이지네이션 안전장치. 무한 루프를 만들지 않는다.
_MAX_PAGES = 100


class ConnectorError(RuntimeError):
    """커넥터 연결·수집 실패. 메시지에 자격증명을 담지 않는다."""


@dataclass(frozen=True)
class AwsAuth:
    """복호화된 AWS 연결 정보. 이 객체를 로그로 찍지 않는다."""

    auth_type: str
    region: str = DEFAULT_REGION
    role_arn: str | None = None
    external_id: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None

    def __repr__(self) -> str:
        """자격증명이 실수로 출력되지 않도록 표현을 고정한다."""
        return f"AwsAuth(auth_type={self.auth_type!r}, region={self.region!r})"

    __str__ = __repr__


@dataclass(frozen=True)
class CheckOutcome:
    """점검 1개의 결과."""

    check_id: str
    status: EvidenceStatus
    payload: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# 마스킹
# --------------------------------------------------------------------------


def mask_account_id(account_id: str) -> str:
    """AWS 계정 ID 를 마스킹한다(뒤 4자리만 남긴다)."""
    text = (account_id or "").strip()
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * (len(text) - 4) + text[-4:]


def mask_arn(arn: str | None) -> str | None:
    """ARN 안의 계정 ID 만 마스킹한다. 역할 이름은 식별에 필요하므로 남긴다."""
    if not arn:
        return None
    parts = arn.split(":")
    # arn:aws:iam::123456789012:role/CertPilotReadOnly → 5번째 필드가 계정 ID.
    if len(parts) >= 6 and parts[4]:
        parts[4] = mask_account_id(parts[4])
        return ":".join(parts)
    return arn


def mask_access_key_id(access_key_id: str | None) -> str | None:
    """액세스 키 ID 를 마스킹한다(앞 4자·뒤 4자만 남긴다)."""
    if not access_key_id:
        return None
    text = access_key_id.strip()
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}{'*' * (len(text) - 8)}{text[-4:]}"


# --------------------------------------------------------------------------
# 연결
# --------------------------------------------------------------------------


def parse_auth(config: dict[str, Any]) -> AwsAuth:
    """복호화된 자격증명 딕셔너리를 `AwsAuth` 로 검증·변환한다."""
    auth_type = str(config.get("auth_type") or "").strip()
    if auth_type not in AUTH_TYPES:
        raise ConnectorError(f"지원하지 않는 연결 방식이다: {auth_type or '(없음)'}")

    region = str(config.get("region") or DEFAULT_REGION).strip() or DEFAULT_REGION

    if auth_type == AUTH_TYPE_ROLE:
        role_arn = str(config.get("role_arn") or "").strip()
        external_id = str(config.get("external_id") or "").strip()
        if not role_arn or not external_id:
            raise ConnectorError("역할 연결에는 role_arn 과 external_id 가 모두 필요하다")
        return AwsAuth(
            auth_type=auth_type, region=region, role_arn=role_arn, external_id=external_id
        )

    access_key_id = str(config.get("access_key_id") or "").strip()
    secret_access_key = str(config.get("secret_access_key") or "").strip()
    if not access_key_id or not secret_access_key:
        raise ConnectorError("액세스 키 연결에는 access_key_id 와 secret_access_key 가 필요하다")
    return AwsAuth(
        auth_type=auth_type,
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )


def build_client_factory(auth: AwsAuth) -> ClientFactory:
    """연결 정보로 boto3 클라이언트 팩토리를 만든다.

    `role` 방식은 여기서 한 번 AssumeRole 해 임시 자격증명을 받고, 그 세션으로
    모든 서비스 클라이언트를 만든다.
    """
    try:
        if auth.auth_type == AUTH_TYPE_ACCESS_KEY:
            session = boto3.Session(
                aws_access_key_id=auth.access_key_id,
                aws_secret_access_key=auth.secret_access_key,
                region_name=auth.region,
            )
        else:
            base = boto3.Session(region_name=auth.region)
            sts = base.client("sts", region_name=auth.region)
            assumed = sts.assume_role(
                RoleArn=auth.role_arn,
                RoleSessionName=ROLE_SESSION_NAME,
                ExternalId=auth.external_id,
                DurationSeconds=ROLE_SESSION_SECONDS,
            )
            credentials = assumed["Credentials"]
            session = boto3.Session(
                aws_access_key_id=credentials["AccessKeyId"],
                aws_secret_access_key=credentials["SecretAccessKey"],
                aws_session_token=credentials["SessionToken"],
                region_name=auth.region,
            )
    except (ClientError, BotoCoreError) as error:
        # 원본 메시지에 자격증명이 섞일 수 있으므로 코드만 남긴다.
        raise ConnectorError(f"AWS 연결에 실패했다({error_code(error)})") from error

    def factory(service: str) -> Any:
        # boto3 타입 스텁은 서비스 이름을 리터럴로 요구한다. 여기서는 동적이라 Any 로 받는다.
        create_client: Any = session.client
        return create_client(service, region_name=auth.region)

    return factory


def test_connection(clients: ClientFactory) -> str:
    """`sts:GetCallerIdentity` 로 연결을 확인하고 마스킹된 계정 ID 를 돌려준다."""
    try:
        identity = clients("sts").get_caller_identity()
    except (ClientError, BotoCoreError) as error:
        raise ConnectorError(f"AWS 연결 확인에 실패했다({error_code(error)})") from error
    return mask_account_id(str(identity.get("Account") or ""))


def error_code(error: Exception) -> str:
    """AWS 예외에서 안전하게 노출할 수 있는 코드만 뽑는다(원문 메시지는 버린다)."""
    if isinstance(error, ClientError):
        response = getattr(error, "response", None) or {}
        code = str((response.get("Error") or {}).get("Code") or "")
        if code:
            return code
    return type(error).__name__


# --------------------------------------------------------------------------
# 페이지네이션
# --------------------------------------------------------------------------


def _collect_pages(
    operation: Callable[..., dict[str, Any]],
    result_key: str,
    *,
    request_token_key: str,
    response_token_key: str,
    truncated_key: str | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """토큰 기반 페이지네이션을 직접 돌린다(스텁 클라이언트에서도 그대로 동작한다)."""
    items: list[dict[str, Any]] = []
    token: str | None = None
    for _ in range(_MAX_PAGES):
        call_kwargs = dict(kwargs)
        if token:
            call_kwargs[request_token_key] = token
        page = operation(**call_kwargs)
        items.extend(page.get(result_key) or [])
        if truncated_key is not None and not page.get(truncated_key):
            break
        token = page.get(response_token_key)
        if not token:
            break
    return items


def _list_users(iam: Any) -> list[dict[str, Any]]:
    """IAM 사용자 전체."""
    return _collect_pages(
        iam.list_users,
        "Users",
        request_token_key="Marker",
        response_token_key="Marker",
        truncated_key="IsTruncated",
    )


def _has_console_access(iam: Any, user_name: str) -> bool:
    """콘솔 로그인 프로필이 있는 사용자인지 본다."""
    try:
        iam.get_login_profile(UserName=user_name)
    except ClientError as error:
        if error_code(error) in _NOT_CONFIGURED_CODES:
            return False
        raise
    return True


# --------------------------------------------------------------------------
# 점검 10개 (PRD §9 표)
# --------------------------------------------------------------------------


def check_iam_root_mfa(clients: ClientFactory) -> CheckOutcome:
    """루트 계정 MFA 설정 여부."""
    summary = clients("iam").get_account_summary().get("SummaryMap") or {}
    enabled = int(summary.get("AccountMFAEnabled") or 0)
    return CheckOutcome(
        check_id="aws.iam.root_mfa",
        status=EvidenceStatus.PASS if enabled == 1 else EvidenceStatus.FAIL,
        payload={"account_mfa_enabled": enabled},
    )


def check_iam_user_mfa(clients: ClientFactory) -> CheckOutcome:
    """콘솔 사용자 MFA 설정 비율."""
    iam = clients("iam")
    users = _list_users(iam)

    console_users: list[str] = []
    with_mfa: list[str] = []
    for user in users:
        name = str(user.get("UserName") or "")
        if not name or not _has_console_access(iam, name):
            continue
        console_users.append(name)
        devices = _collect_pages(
            iam.list_mfa_devices,
            "MFADevices",
            request_token_key="Marker",
            response_token_key="Marker",
            truncated_key="IsTruncated",
            UserName=name,
        )
        if devices:
            with_mfa.append(name)

    missing = sorted(set(console_users) - set(with_mfa))
    return CheckOutcome(
        check_id="aws.iam.user_mfa",
        status=EvidenceStatus.PASS if not missing else EvidenceStatus.FAIL,
        payload={
            "users": len(users),
            "console_users": len(console_users),
            "mfa_enabled": len(with_mfa),
            "missing": missing,
        },
    )


def check_iam_password_policy(clients: ClientFactory) -> CheckOutcome:
    """계정 비밀번호 정책(길이·복잡도·만료)."""
    try:
        policy = clients("iam").get_account_password_policy().get("PasswordPolicy") or {}
    except ClientError as error:
        if error_code(error) not in _NOT_CONFIGURED_CODES:
            raise
        return CheckOutcome(
            check_id="aws.iam.password_policy",
            status=EvidenceStatus.FAIL,
            payload={"policy_exists": False, "reasons": ["계정 비밀번호 정책이 설정돼 있지 않다"]},
        )

    min_length = int(policy.get("MinimumPasswordLength") or 0)
    complexity = all(
        bool(policy.get(key))
        for key in (
            "RequireUppercaseCharacters",
            "RequireLowercaseCharacters",
            "RequireNumbers",
            "RequireSymbols",
        )
    )
    max_age = int(policy.get("MaxPasswordAge") or 0)

    reasons: list[str] = []
    if min_length < MIN_PASSWORD_LENGTH:
        reasons.append(f"최소 길이가 {min_length}자로 {MIN_PASSWORD_LENGTH}자에 못 미친다")
    if not complexity:
        reasons.append("영대문자·소문자·숫자·특수문자를 모두 요구하지 않는다")
    if max_age <= 0:
        reasons.append("비밀번호 만료 주기가 설정돼 있지 않다")

    return CheckOutcome(
        check_id="aws.iam.password_policy",
        status=EvidenceStatus.PASS if not reasons else EvidenceStatus.FAIL,
        payload={
            "policy_exists": True,
            "minimum_password_length": min_length,
            "complexity_required": complexity,
            "max_password_age": max_age,
            "reasons": reasons,
        },
    )


def _key_age_days(created: Any, now: datetime) -> int | None:
    """액세스 키 생성 시각에서 경과 일수를 구한다."""
    if not isinstance(created, datetime):
        return None
    reference = created if created.tzinfo else created.replace(tzinfo=UTC)
    return (now - reference).days


def check_iam_key_age(clients: ClientFactory) -> CheckOutcome:
    """90일이 지난 활성 액세스 키."""
    iam = clients("iam")
    now = datetime.now(UTC)

    active = 0
    expired: list[dict[str, Any]] = []
    for user in _list_users(iam):
        name = str(user.get("UserName") or "")
        if not name:
            continue
        keys = _collect_pages(
            iam.list_access_keys,
            "AccessKeyMetadata",
            request_token_key="Marker",
            response_token_key="Marker",
            truncated_key="IsTruncated",
            UserName=name,
        )
        for key in keys:
            if str(key.get("Status") or "") != "Active":
                continue
            active += 1
            age = _key_age_days(key.get("CreateDate"), now)
            if age is not None and age > MAX_ACCESS_KEY_AGE_DAYS:
                expired.append(
                    {
                        "user": name,
                        # 액세스 키 ID 는 마스킹해서 남긴다(식별만 가능하게).
                        "access_key_id": mask_access_key_id(str(key.get("AccessKeyId") or "")),
                        "age_days": age,
                    }
                )

    return CheckOutcome(
        check_id="aws.iam.key_age",
        status=EvidenceStatus.PASS if not expired else EvidenceStatus.FAIL,
        payload={
            "active_keys": active,
            "expired_keys": expired,
            "max_age_days": MAX_ACCESS_KEY_AGE_DAYS,
        },
    )


def check_iam_admin_users(clients: ClientFactory) -> CheckOutcome:
    """AdministratorAccess 정책이 직접 붙은 사용자."""
    iam = clients("iam")

    admins: list[str] = []
    for user in _list_users(iam):
        name = str(user.get("UserName") or "")
        if not name:
            continue
        policies = _collect_pages(
            iam.list_attached_user_policies,
            "AttachedPolicies",
            request_token_key="Marker",
            response_token_key="Marker",
            truncated_key="IsTruncated",
            UserName=name,
        )
        if any(str(policy.get("PolicyName") or "") == ADMIN_POLICY_NAME for policy in policies):
            admins.append(name)

    admins.sort()
    if not admins:
        status = EvidenceStatus.PASS
    elif len(admins) <= ADMIN_USER_WARN_LIMIT:
        status = EvidenceStatus.WARN
    else:
        status = EvidenceStatus.FAIL

    return CheckOutcome(
        check_id="aws.iam.admin_users",
        status=status,
        payload={
            "admin_user_count": len(admins),
            "admin_users": admins,
            "warn_limit": ADMIN_USER_WARN_LIMIT,
        },
    )


def check_cloudtrail_enabled(clients: ClientFactory) -> CheckOutcome:
    """전 리전 트레일 + 로그 파일 무결성 검증."""
    trails = clients("cloudtrail").describe_trails().get("trailList") or []

    compliant = [
        str(trail.get("Name") or "")
        for trail in trails
        if trail.get("IsMultiRegionTrail") and trail.get("LogFileValidationEnabled")
    ]
    return CheckOutcome(
        check_id="aws.cloudtrail.enabled",
        status=EvidenceStatus.PASS if compliant else EvidenceStatus.FAIL,
        payload={
            "trails": len(trails),
            "compliant_trails": sorted(compliant),
            "multi_region": sum(1 for trail in trails if trail.get("IsMultiRegionTrail")),
            "log_file_validation": sum(
                1 for trail in trails if trail.get("LogFileValidationEnabled")
            ),
        },
    )


def _bucket_names(s3: Any) -> list[str]:
    """버킷 이름 목록."""
    buckets = s3.list_buckets().get("Buckets") or []
    return [str(bucket.get("Name") or "") for bucket in buckets if bucket.get("Name")]


def _resolve_bucket_status(
    *, violations: Iterable[Any], errors: Iterable[Any]
) -> EvidenceStatus:
    """버킷 단위 점검의 종합 상태. 위반이 있으면 fail, 확인 불가만 있으면 unknown."""
    if list(violations):
        return EvidenceStatus.FAIL
    if list(errors):
        return EvidenceStatus.UNKNOWN
    return EvidenceStatus.PASS


def check_s3_public_block(clients: ClientFactory) -> CheckOutcome:
    """버킷 퍼블릭 액세스 차단 4개 옵션."""
    s3 = clients("s3")
    names = _bucket_names(s3)

    unblocked: list[str] = []
    blocked = 0
    errors: list[dict[str, str]] = []
    for name in names:
        try:
            config = s3.get_public_access_block(Bucket=name).get(
                "PublicAccessBlockConfiguration"
            ) or {}
        except ClientError as error:
            code = error_code(error)
            if code in _NOT_CONFIGURED_CODES:
                unblocked.append(name)
                continue
            errors.append({"bucket": name, "error": code})
            continue
        if all(bool(config.get(key)) for key in PUBLIC_ACCESS_BLOCK_KEYS):
            blocked += 1
        else:
            unblocked.append(name)

    return CheckOutcome(
        check_id="aws.s3.public_block",
        status=_resolve_bucket_status(violations=unblocked, errors=errors),
        payload={
            "buckets": len(names),
            "blocked": blocked,
            "unblocked": sorted(unblocked),
            "errors": errors,
        },
    )


def check_s3_encryption(clients: ClientFactory) -> CheckOutcome:
    """버킷 기본 서버 측 암호화."""
    s3 = clients("s3")
    names = _bucket_names(s3)

    unencrypted: list[str] = []
    encrypted = 0
    errors: list[dict[str, str]] = []
    for name in names:
        try:
            config = s3.get_bucket_encryption(Bucket=name).get(
                "ServerSideEncryptionConfiguration"
            ) or {}
        except ClientError as error:
            code = error_code(error)
            if code in _NOT_CONFIGURED_CODES:
                unencrypted.append(name)
                continue
            errors.append({"bucket": name, "error": code})
            continue
        if config.get("Rules"):
            encrypted += 1
        else:
            unencrypted.append(name)

    return CheckOutcome(
        check_id="aws.s3.encryption",
        status=_resolve_bucket_status(violations=unencrypted, errors=errors),
        payload={
            "buckets": len(names),
            "encrypted": encrypted,
            "unencrypted": sorted(unencrypted),
            "errors": errors,
        },
    )


def check_rds_encryption(clients: ClientFactory) -> CheckOutcome:
    """RDS 저장 암호화와 자동 백업 보관 기간."""
    instances = _collect_pages(
        clients("rds").describe_db_instances,
        "DBInstances",
        request_token_key="Marker",
        response_token_key="Marker",
    )

    violations: list[dict[str, Any]] = []
    for instance in instances:
        encrypted = bool(instance.get("StorageEncrypted"))
        retention = int(instance.get("BackupRetentionPeriod") or 0)
        if encrypted and retention >= MIN_BACKUP_RETENTION_DAYS:
            continue
        violations.append(
            {
                "db_instance": str(instance.get("DBInstanceIdentifier") or ""),
                "storage_encrypted": encrypted,
                "backup_retention_days": retention,
            }
        )

    return CheckOutcome(
        check_id="aws.rds.encryption",
        status=EvidenceStatus.PASS if not violations else EvidenceStatus.FAIL,
        payload={
            "instances": len(instances),
            "compliant": len(instances) - len(violations),
            "violations": violations,
            "min_backup_retention_days": MIN_BACKUP_RETENTION_DAYS,
        },
    )


def _covered_ports(permission: dict[str, Any]) -> list[int]:
    """인바운드 규칙이 열어 주는 민감 포트 목록."""
    protocol = str(permission.get("IpProtocol") or "")
    if protocol == "-1":
        return list(SENSITIVE_PORTS)
    if protocol not in {"tcp", "6"}:
        return []
    from_port = permission.get("FromPort")
    to_port = permission.get("ToPort")
    if from_port is None or to_port is None:
        return []
    low, high = int(from_port), int(to_port)
    return [port for port in SENSITIVE_PORTS if low <= port <= high]


def _open_cidrs(permission: dict[str, Any]) -> list[str]:
    """전체 개방(0.0.0.0/0 · ::/0) CIDR 목록."""
    found = [
        str(entry.get("CidrIp"))
        for entry in permission.get("IpRanges") or []
        if str(entry.get("CidrIp") or "") in OPEN_CIDRS
    ]
    found.extend(
        str(entry.get("CidrIpv6"))
        for entry in permission.get("Ipv6Ranges") or []
        if str(entry.get("CidrIpv6") or "") in OPEN_CIDRS_V6
    )
    return found


def check_ec2_open_sg(clients: ClientFactory) -> CheckOutcome:
    """0.0.0.0/0 으로 열린 22 / 3389 / 3306 / 5432 인바운드 규칙."""
    groups = _collect_pages(
        clients("ec2").describe_security_groups,
        "SecurityGroups",
        request_token_key="NextToken",
        response_token_key="NextToken",
    )

    open_rules: list[dict[str, Any]] = []
    for group in groups:
        for permission in group.get("IpPermissions") or []:
            cidrs = _open_cidrs(permission)
            if not cidrs:
                continue
            for port in _covered_ports(permission):
                for cidr in cidrs:
                    open_rules.append(
                        {
                            "group_id": str(group.get("GroupId") or ""),
                            "group_name": str(group.get("GroupName") or ""),
                            "port": port,
                            "cidr": cidr,
                        }
                    )

    return CheckOutcome(
        check_id="aws.ec2.open_sg",
        status=EvidenceStatus.PASS if not open_rules else EvidenceStatus.FAIL,
        payload={
            "security_groups": len(groups),
            "open_rules": open_rules,
            "checked_ports": list(SENSITIVE_PORTS),
        },
    )


CheckFunction = Callable[[ClientFactory], CheckOutcome]

# check_id → 점검 함수. 매핑(항목 코드·표시명)은 코드가 아니라 YAML 에 있다.
CHECK_FUNCTIONS: dict[str, CheckFunction] = {
    "aws.iam.root_mfa": check_iam_root_mfa,
    "aws.iam.user_mfa": check_iam_user_mfa,
    "aws.iam.password_policy": check_iam_password_policy,
    "aws.iam.key_age": check_iam_key_age,
    "aws.iam.admin_users": check_iam_admin_users,
    "aws.cloudtrail.enabled": check_cloudtrail_enabled,
    "aws.s3.public_block": check_s3_public_block,
    "aws.s3.encryption": check_s3_encryption,
    "aws.rds.encryption": check_rds_encryption,
    "aws.ec2.open_sg": check_ec2_open_sg,
}


def run_check(check_id: str, clients: ClientFactory) -> CheckOutcome:
    """점검 1개를 실행한다. 실패해도 예외를 밖으로 던지지 않고 unknown 으로 남긴다.

    한 점검이 권한 부족으로 실패해도 나머지 9개는 계속 수집해야 한다(PRD §7 F5).
    사유는 payload 에 코드로만 남긴다 — 원본 예외 메시지에는 자격증명이 섞일 수 있다.
    """
    function = CHECK_FUNCTIONS.get(check_id)
    if function is None:
        return CheckOutcome(
            check_id=check_id,
            status=EvidenceStatus.UNKNOWN,
            payload={"reason": "아직 구현되지 않은 점검이다"},
        )

    try:
        return function(clients)
    except (ClientError, BotoCoreError) as error:
        code = error_code(error)
        logger.warning("AWS 점검 실패: check_id=%s 오류=%s", check_id, code)
        return CheckOutcome(
            check_id=check_id,
            status=EvidenceStatus.UNKNOWN,
            payload={"reason": f"AWS 호출에 실패했다({code})", "error": code},
        )
    except Exception as error:  # noqa: BLE001 - 점검 1개의 실패가 수집 전체를 막지 않는다
        code = type(error).__name__
        logger.warning("AWS 점검 처리 실패: check_id=%s 오류=%s", check_id, code, exc_info=True)
        return CheckOutcome(
            check_id=check_id,
            status=EvidenceStatus.UNKNOWN,
            payload={"reason": f"점검을 처리하지 못했다({code})", "error": code},
        )


def run_all_checks(clients: ClientFactory) -> list[CheckOutcome]:
    """매핑 파일에 정의된 순서대로 점검을 전부 실행한다."""
    mappings: dict[str, CheckMapping] = load_check_mappings()
    return [run_check(check_id, clients) for check_id in mappings]
