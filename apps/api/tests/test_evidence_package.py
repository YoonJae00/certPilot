"""증적 패키지 ZIP 테스트 (PRD §7 F7).

AC: ZIP 이 생성되고, 항목 폴더에 판정·증적·문서 출처가 들어가며, 계정 ID·시크릿
원문이 패키지 어디에도 남지 않는다.

아래 계정 ID·키·시크릿은 전부 지어낸 가짜 값이다(CLAUDE.md 절대 규칙 3). 실제
자격증명을 픽스처에 넣지 않는다.
"""

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    Assessment,
    AssessmentStatus,
    AuditLog,
    Chunk,
    DecidedBy,
    Document,
    DocumentStatus,
    Evidence,
    EvidenceStatus,
    Finding,
    FindingStatus,
)
from app.services.criteria_loader import seed_criteria
from app.services.evidence_package import MASKED_SECRET
from tests.conftest import login

# 전부 가짜 값이다. 실제 계정·키가 아니다.
FAKE_ACCOUNT_ID = "123456789012"
FAKE_ROLE_ARN = f"arn:aws:iam::{FAKE_ACCOUNT_ID}:role/CertPilotReadOnly"
FAKE_ACCESS_KEY_ID = "AKIAEXAMPLEFAKEKEY01"
FAKE_SESSION_TOKEN = "fake-session-token-value-do-not-use"

# 근거가 붙는 항목과 붙지 않는 항목.
UNMET_CODE = "2.5.3"
MET_CODE = "2.9.4"
UNKNOWN_CODE = "1.1.1"

# 청크 본문에 넣는 가짜 연락처. 마스킹 재확인용이다.
FAKE_PHONE = "010-1234-5678"


@pytest.fixture
def packaged(client, db, tenants) -> dict:
    """완료된 모의심사 1건 + 문서 청크 + 클라우드 증적."""
    seed_criteria(db)
    db.commit()

    project = tenants["project_a"]
    now = datetime.now(UTC)

    document = Document(
        project_id=project.id,
        filename="정보보호정책_v2.1.pdf",
        s3_key=f"projects/{project.id}/policy.pdf",
        mime="application/pdf",
        status=DocumentStatus.PARSED,
        page_count=20,
        sha256="a" * 64,
    )
    db.add(document)
    db.flush()

    unmet_chunk = Chunk(
        document_id=document.id,
        seq=0,
        page=7,
        text=(
            "사용자 인증은 아이디와 비밀번호만으로 수행하며 추가 인증 수단을 두지 않는다. "
            f"문의처 {FAKE_PHONE}."
        ),
    )
    met_chunk = Chunk(
        document_id=document.id,
        seq=1,
        page=13,
        text="접속기록은 6개월간 보관하고 매월 검토한다.",
    )
    db.add_all([unmet_chunk, met_chunk])

    fail_evidence = Evidence(
        project_id=project.id,
        source="aws.iam",
        check_id="aws.iam.root_mfa",
        criterion_codes=[UNMET_CODE],
        status=EvidenceStatus.FAIL,
        payload_json={
            "account_id": FAKE_ACCOUNT_ID,
            "role_arn": FAKE_ROLE_ARN,
            "access_key_id": FAKE_ACCESS_KEY_ID,
            "session_token": FAKE_SESSION_TOKEN,
            "account_mfa_enabled": 0,
            "users": [{"name": "operator", "account_id": FAKE_ACCOUNT_ID}],
        },
        collected_at=now - timedelta(hours=2),
    )
    pass_evidence = Evidence(
        project_id=project.id,
        source="aws.cloudtrail",
        check_id="aws.cloudtrail.enabled",
        criterion_codes=[MET_CODE],
        status=EvidenceStatus.PASS,
        payload_json={"trails": 1, "multi_region": True},
        collected_at=now - timedelta(hours=1),
    )
    db.add_all([fail_evidence, pass_evidence])
    db.flush()

    assessment = Assessment(
        project_id=project.id,
        status=AssessmentStatus.DONE,
        started_at=now - timedelta(minutes=20),
        finished_at=now - timedelta(minutes=10),
        model="stub-model",
    )
    db.add(assessment)
    db.flush()

    db.add_all(
        [
            Finding(
                assessment_id=assessment.id,
                criterion_code=UNMET_CODE,
                status=FindingStatus.UNMET,
                confidence=0.91,
                rationale="정책에 추가 인증 수단이 없고, 루트 계정 MFA 점검도 미충족이다.",
                evidence_chunk_ids=[str(unmet_chunk.id)],
                evidence_ids=[str(fail_evidence.id)],
                predicted_defect="사용자 인증 수단이 단일 요소다.",
                recommendation="관리자 계정에 MFA 를 적용한다.",
                decided_by=DecidedBy.RULE,
            ),
            Finding(
                assessment_id=assessment.id,
                criterion_code=MET_CODE,
                status=FindingStatus.MET,
                confidence=0.84,
                rationale="접속기록 보관·검토 절차가 문서와 증적으로 확인된다.",
                evidence_chunk_ids=[str(met_chunk.id)],
                evidence_ids=[str(pass_evidence.id)],
                predicted_defect=None,
                recommendation=None,
                decided_by=DecidedBy.LLM,
            ),
            Finding(
                assessment_id=assessment.id,
                criterion_code=UNKNOWN_CODE,
                status=FindingStatus.UNKNOWN,
                confidence=0.0,
                rationale="근거를 찾지 못했다.",
                evidence_chunk_ids=[],
                evidence_ids=[],
                predicted_defect=None,
                recommendation=None,
                decided_by=DecidedBy.LLM,
            ),
        ]
    )
    db.commit()

    login(client, "admin-a@example.com")
    return {
        "project_id": project.id,
        "assessment_id": assessment.id,
        "document": document,
    }


def download(client, packaged) -> zipfile.ZipFile:
    """증적 패키지를 내려받아 ZipFile 로 연다."""
    response = client.get(
        f"/projects/{packaged['project_id']}"
        f"/assessments/{packaged['assessment_id']}/evidence-package.zip"
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "evidence-package" in response.headers["content-disposition"]
    return zipfile.ZipFile(io.BytesIO(response.content))


def read_all_text(archive: zipfile.ZipFile) -> str:
    """패키지 안의 모든 파일 본문을 이어 붙인다(마스킹 검증용)."""
    return "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())


def test_package_has_readme_and_item_folders(client, packaged):
    """README 와 근거가 있는 항목 폴더가 들어 있다."""
    archive = download(client, packaged)
    names = archive.namelist()

    assert "README.md" in names
    assert f"{UNMET_CODE}/finding.json" in names
    assert f"{UNMET_CODE}/document_sources.md" in names
    assert f"{MET_CODE}/finding.json" in names
    assert any(name.startswith(f"{UNMET_CODE}/evidence_") for name in names)

    readme = archive.read("README.md").decode("utf-8")
    assert "증적 패키지" in readme
    assert "마스킹" in readme
    assert "폴더 구조" in readme
    assert str(packaged["assessment_id"]) in readme


def test_package_finding_json_is_parsable(client, packaged):
    """finding.json 은 판정 요약을 그대로 담는다."""
    archive = download(client, packaged)
    payload = json.loads(archive.read(f"{UNMET_CODE}/finding.json").decode("utf-8"))

    assert payload["criterion_code"] == UNMET_CODE
    assert payload["status"] == "unmet"
    assert payload["status_label"] == "미충족"
    assert payload["confidence"] == pytest.approx(0.91)
    assert payload["decided_by"] == "rule"
    assert payload["rationale"]
    assert payload["evidence_count"] == 1
    assert payload["document_source_count"] == 1


def test_package_document_sources_list_filename_page_and_excerpt(client, packaged):
    """문서 출처에는 문서명·페이지·발췌가 들어간다(개인정보는 마스킹)."""
    archive = download(client, packaged)
    sources = archive.read(f"{UNMET_CODE}/document_sources.md").decode("utf-8")

    assert "정보보호정책_v2.1.pdf" in sources
    assert "7쪽" in sources
    assert "추가 인증 수단을 두지 않는다" in sources
    assert "[MASKED:phone]" in sources
    assert FAKE_PHONE not in sources


def test_package_omits_items_without_evidence(client, packaged):
    """근거가 없는 판단불가 항목은 폴더를 만들지 않고 README 목록에만 남는다."""
    archive = download(client, packaged)
    names = archive.namelist()

    assert not any(name.startswith(f"{UNKNOWN_CODE}/") for name in names)

    readme = archive.read("README.md").decode("utf-8")
    assert f"- {UNKNOWN_CODE} " in readme
    assert "판단불가" in readme


def test_package_masks_account_ids_and_secrets(client, packaged):
    """패키지 전문에 마스킹 전 계정 ID·액세스 키·시크릿이 남지 않는다."""
    archive = download(client, packaged)
    everything = read_all_text(archive)

    assert FAKE_ACCOUNT_ID not in everything
    assert FAKE_ACCESS_KEY_ID not in everything
    assert FAKE_SESSION_TOKEN not in everything
    assert FAKE_ROLE_ARN not in everything

    evidence_name = next(
        name for name in archive.namelist() if name.startswith(f"{UNMET_CODE}/evidence_")
    )
    evidence = json.loads(archive.read(evidence_name).decode("utf-8"))
    payload = evidence["payload"]

    assert payload["account_id"].endswith("9012")
    assert payload["account_id"] != FAKE_ACCOUNT_ID
    assert payload["role_arn"].startswith("arn:aws:iam::")
    assert "CertPilotReadOnly" in payload["role_arn"]
    assert payload["session_token"] == MASKED_SECRET
    # 숫자 값은 마스킹 대상이 아니다(수치가 사라지면 증적이 쓸모없어진다).
    assert payload["account_mfa_enabled"] == 0
    assert payload["users"][0]["account_id"] != FAKE_ACCOUNT_ID

    assert evidence["check_title"] == "루트 계정 MFA"
    assert evidence["masked"] is True


def test_package_requires_done_assessment(client, db, packaged):
    """완료되지 않은 모의심사는 409 다."""
    running = Assessment(project_id=packaged["project_id"], status=AssessmentStatus.RUNNING)
    db.add(running)
    db.commit()

    response = client.get(
        f"/projects/{packaged['project_id']}/assessments/{running.id}/evidence-package.zip"
    )
    assert response.status_code == 409


def test_package_writes_audit_log(client, db, packaged):
    """증적 패키지 다운로드가 감사 로그에 남는다."""
    download(client, packaged)

    log = db.execute(
        select(AuditLog).where(
            AuditLog.action == "assessment.evidence_package_download",
            AuditLog.target == str(packaged["assessment_id"]),
        )
    ).scalar_one()
    assert log.meta_json["bytes"] > 0


def test_package_rejects_other_org_and_reviewer(client, packaged, tenants):
    """다른 조직 프로젝트는 404, 심사원은 403 이다."""
    other = client.get(
        f"/projects/{tenants['project_b'].id}"
        f"/assessments/{packaged['assessment_id']}/evidence-package.zip"
    )
    assert other.status_code == 404

    login(client, "reviewer@example.com")
    forbidden = client.get(
        f"/projects/{packaged['project_id']}"
        f"/assessments/{packaged['assessment_id']}/evidence-package.zip"
    )
    assert forbidden.status_code == 403
