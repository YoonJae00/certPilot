"""유지 대시보드·알림 API 테스트 (PRD §7 F8).

AC: 시드 데이터 기준 숫자 검증. 준비도는 파이프라인이 만든 `summary_json` 과 같은
값이어야 한다(대시보드는 DB 행에서 직접 집계하므로, 두 값이 어긋나면 여기서 잡힌다).

픽스처의 계정·수치는 전부 가짜다. 실제 자격증명·개인정보를 넣지 않는다.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    Alert,
    AlertType,
    Assessment,
    AssessmentStatus,
    Criterion,
    DecidedBy,
    Document,
    DocumentStatus,
    Draft,
    DraftKind,
    DraftStatus,
    Evidence,
    EvidenceStatus,
    Finding,
    FindingStatus,
)
from app.services.criteria_loader import seed_criteria
from app.workers.assess import ItemOutcome, build_summary
from tests.conftest import login

# 항목 순서대로 돌려 가며 붙이는 판정. 4개 상태가 모두 나오게 한다.
STATUS_CYCLE = [
    FindingStatus.MET,
    FindingStatus.PARTIAL,
    FindingStatus.UNMET,
    FindingStatus.UNKNOWN,
]

# 사후심사 예정일까지 남긴 일수. D-day 계산 검증에 쓴다.
AUDIT_DUE_IN_DAYS = 45


def _confidence_for(index: int) -> float:
    """확신도를 코드 순서와 같이 올린다.

    뒤쪽 항목일수록 확신도가 높으므로, Top 5 가 삽입 순서가 아니라 확신도 순으로
    정렬됐는지 확인할 수 있다.
    """
    return round(0.10 + index * 0.008, 4)


@pytest.fixture
def seeded(client, db, tenants) -> dict:
    """대시보드 검증용 시드. 모의심사 done 1건 + 문서·증적·알림·검수 대기."""
    seed_criteria(db)
    db.commit()

    project = tenants["project_a"]
    project.audit_due_date = date.today() + timedelta(days=AUDIT_DUE_IN_DAYS)
    db.commit()

    criteria = list(db.execute(select(Criterion).order_by(Criterion.code)).scalars())

    now = datetime.now(UTC)
    assessment = Assessment(
        project_id=project.id,
        status=AssessmentStatus.DONE,
        started_at=now - timedelta(minutes=10),
        finished_at=now - timedelta(minutes=5),
        model="stub-model",
    )
    db.add(assessment)
    db.flush()

    outcomes: list[ItemOutcome] = []
    for index, criterion in enumerate(criteria):
        finding_status = STATUS_CYCLE[index % len(STATUS_CYCLE)]
        # 판단불가에는 근거를 붙이지 않는다(CLAUDE.md 절대 규칙 2와 같은 모양).
        chunk_ids = [] if finding_status is FindingStatus.UNKNOWN else [str(uuid.uuid4())]
        predicted_defect = (
            f"{criterion.code} 예상 결함" if finding_status is FindingStatus.UNMET else None
        )
        confidence = _confidence_for(index)

        db.add(
            Finding(
                assessment_id=assessment.id,
                criterion_code=criterion.code,
                status=finding_status,
                confidence=confidence,
                rationale=f"{criterion.code} 판정 근거(픽스처).",
                evidence_chunk_ids=chunk_ids,
                evidence_ids=[],
                predicted_defect=predicted_defect,
                recommendation=None,
                decided_by=DecidedBy.LLM,
            )
        )
        outcomes.append(
            ItemOutcome(
                code=criterion.code,
                status=finding_status,
                confidence=confidence,
                rationale="",
                chunk_ids=chunk_ids,
                evidence_ids=[],
                predicted_defect=predicted_defect,
                recommendation=None,
                decided_by=DecidedBy.LLM,
            )
        )

    assessment.summary_json = build_summary(
        outcomes,
        {criterion.code: criterion.chapter for criterion in criteria},
        total=len(criteria),
        done=len(criteria),
    )

    db.add_all(
        [
            Document(
                project_id=project.id,
                filename="정보보호정책.pdf",
                s3_key=f"projects/{project.id}/policy.pdf",
                mime="application/pdf",
                status=DocumentStatus.PARSED,
                page_count=12,
                sha256="0" * 64,
            ),
            Document(
                project_id=project.id,
                filename="백업정책.md",
                s3_key=f"projects/{project.id}/backup.md",
                mime="text/markdown",
                status=DocumentStatus.PARSED,
                page_count=None,
                sha256="1" * 64,
            ),
        ]
    )

    latest_collected_at = now - timedelta(hours=1)
    db.add_all(
        [
            Evidence(
                project_id=project.id,
                source="aws.iam",
                check_id="aws.iam.root_mfa",
                criterion_codes=["2.5.3"],
                status=EvidenceStatus.FAIL,
                payload_json={"account_mfa_enabled": 0},
                collected_at=now - timedelta(days=2),
            ),
            Evidence(
                project_id=project.id,
                source="aws.cloudtrail",
                check_id="aws.cloudtrail.enabled",
                criterion_codes=["2.9.4"],
                status=EvidenceStatus.PASS,
                payload_json={"trails": 1, "multi_region": True},
                collected_at=latest_collected_at,
            ),
        ]
    )

    alerts = [
        Alert(
            project_id=project.id,
            type=AlertType.DEFECT,
            message="[2.5.3] 예상 결함이 새로 발견됐다",
            created_at=now - timedelta(hours=6),
            read_at=now - timedelta(hours=5),
        ),
        Alert(
            project_id=project.id,
            type=AlertType.DUE,
            message="사후심사가 45일 남았다",
            created_at=now - timedelta(hours=3),
        ),
        Alert(
            project_id=project.id,
            type=AlertType.DRIFT,
            message="[루트 계정 MFA] 판정이 충족 → 미충족으로 바뀌었다",
            created_at=now - timedelta(hours=1),
        ),
    ]
    db.add_all(alerts)

    db.add_all(
        [
            Draft(project_id=project.id, kind=DraftKind.SOW, version=1, status=DraftStatus.DRAFT),
            Draft(
                project_id=project.id,
                kind=DraftKind.SOW,
                version=2,
                status=DraftStatus.IN_REVIEW,
            ),
            Draft(
                project_id=project.id,
                kind=DraftKind.POLICY,
                version=1,
                status=DraftStatus.IN_REVIEW,
            ),
            Draft(
                project_id=project.id,
                kind=DraftKind.POLICY,
                version=2,
                status=DraftStatus.APPROVED,
            ),
        ]
    )
    db.commit()
    db.refresh(assessment)

    login(client, "admin-a@example.com")
    return {
        "project_id": project.id,
        "assessment_id": assessment.id,
        "summary": dict(assessment.summary_json or {}),
        "criteria": criteria,
        "latest_collected_at": latest_collected_at,
        "alerts": alerts,
    }


def test_dashboard_readiness_matches_summary_json(client, seeded):
    """대시보드 준비도는 DB 집계에서 나오고, 파이프라인 summary_json 과 같은 값이다."""
    response = client.get(f"/projects/{seeded['project_id']}/dashboard")
    assert response.status_code == 200, response.text
    payload = response.json()

    summary = seeded["summary"]
    assert payload["readiness"]["overall"] == pytest.approx(summary["readiness"])

    for chapter, expected in summary["by_chapter"].items():
        actual = payload["readiness"]["by_chapter"][chapter]
        assert actual["total"] == expected["total"]
        assert actual["met"] == expected["met"]
        assert actual["partial"] == expected["partial"]
        assert actual["unmet"] == expected["unmet"]
        assert actual["unknown"] == expected["unknown"]
        assert actual["readiness"] == pytest.approx(expected["readiness"])

    # 장은 1·2·3장 세 개다.
    assert sorted(payload["readiness"]["by_chapter"]) == ["1", "2", "3"]


def test_dashboard_counts_match_database(client, db, seeded):
    """문서 수·검수 대기·미읽음 알림·최근 수집 시각이 DB 집계와 일치한다."""
    project_id = seeded["project_id"]
    payload = client.get(f"/projects/{project_id}/dashboard").json()

    assert payload["document_count"] == 2
    assert payload["pending_review_count"] == 2
    assert payload["unread_alert_count"] == 2
    assert payload["last_collected_at"] is not None
    assert datetime.fromisoformat(payload["last_collected_at"]) == seeded["latest_collected_at"]

    unmet_in_db = db.execute(
        select(Finding).where(
            Finding.assessment_id == seeded["assessment_id"],
            Finding.status == FindingStatus.UNMET,
        )
    ).scalars()
    assert payload["readiness"]["by_chapter"]["1"]["unmet"] >= 0
    assert len(list(unmet_in_db)) == sum(
        chapter["unmet"] for chapter in payload["readiness"]["by_chapter"].values()
    )

    assert payload["last_assessment"]["id"] == str(seeded["assessment_id"])
    assert payload["last_assessment"]["status"] == "done"
    assert payload["last_assessment"]["finished_at"] is not None


def test_dashboard_top_unmet_is_sorted_and_capped(client, seeded):
    """미충족 Top 5 는 확신도 내림차순으로 5개까지만 준다."""
    payload = client.get(f"/projects/{seeded['project_id']}/dashboard").json()
    top = payload["top_unmet"]

    assert len(top) == 5
    confidences = [item["confidence"] for item in top]
    assert confidences == sorted(confidences, reverse=True)

    criteria = seeded["criteria"]
    # 미충족은 인덱스 4k+2 이고 확신도는 인덱스에 비례하므로, 뒤쪽 5개가 올라온다.
    unmet_indexes = [index for index in range(len(criteria)) if index % 4 == 2]
    expected_codes = [criteria[index].code for index in reversed(unmet_indexes[-5:])]
    assert [item["criterion_code"] for item in top] == expected_codes

    by_code = {criterion.code: criterion for criterion in criteria}
    for item in top:
        assert item["title"] == by_code[item["criterion_code"]].title
        assert item["predicted_defect"] == f"{item['criterion_code']} 예상 결함"


def test_dashboard_audit_due_d_day(client, db, seeded, tenants):
    """사후심사 D-day 는 오늘 기준 남은 일수다. 예정일이 없으면 null."""
    payload = client.get(f"/projects/{seeded['project_id']}/dashboard").json()
    assert payload["audit_due"]["d_day"] == AUDIT_DUE_IN_DAYS
    assert payload["audit_due"]["date"] == (
        date.today() + timedelta(days=AUDIT_DUE_IN_DAYS)
    ).isoformat()

    project = tenants["project_a"]
    project.audit_due_date = None
    db.commit()

    payload = client.get(f"/projects/{seeded['project_id']}/dashboard").json()
    assert payload["audit_due"] is None


def test_dashboard_recent_alerts_are_newest_first(client, seeded):
    """최근 알림은 최신순 5개까지다."""
    payload = client.get(f"/projects/{seeded['project_id']}/dashboard").json()
    alerts = payload["recent_alerts"]

    assert [item["type"] for item in alerts] == ["drift", "due", "defect"]
    assert alerts[0]["read_at"] is None
    assert alerts[-1]["read_at"] is not None


def test_dashboard_without_done_assessment_has_null_readiness(client, db, tenants):
    """완료된 모의심사가 없으면 준비도는 null 이고 Top 5 는 비어 있다."""
    seed_criteria(db)
    project = tenants["project_a"]
    assessment = Assessment(project_id=project.id, status=AssessmentStatus.RUNNING)
    db.add(assessment)
    db.commit()

    login(client, "admin-a@example.com")
    payload = client.get(f"/projects/{project.id}/dashboard").json()

    assert payload["readiness"] is None
    assert payload["top_unmet"] == []
    assert payload["document_count"] == 0
    assert payload["last_collected_at"] is None
    assert payload["last_assessment"]["status"] == "running"


def test_dashboard_rejects_other_org_and_reviewer(client, seeded, tenants):
    """다른 조직 프로젝트는 404, 심사원은 403 이다."""
    other = client.get(f"/projects/{tenants['project_b'].id}/dashboard")
    assert other.status_code == 404

    login(client, "reviewer@example.com")
    forbidden = client.get(f"/projects/{seeded['project_id']}/dashboard")
    assert forbidden.status_code == 403


def test_list_alerts_supports_filters(client, seeded):
    """알림 목록은 최신순이고 종류·미읽음·개수 필터를 받는다."""
    project_id = seeded["project_id"]

    everything = client.get(f"/projects/{project_id}/alerts")
    assert everything.status_code == 200
    assert [item["type"] for item in everything.json()] == ["drift", "due", "defect"]

    drift = client.get(f"/projects/{project_id}/alerts", params={"type": "drift"})
    assert [item["type"] for item in drift.json()] == ["drift"]

    unread = client.get(f"/projects/{project_id}/alerts", params={"unread_only": True})
    assert len(unread.json()) == 2
    assert all(item["read_at"] is None for item in unread.json())

    limited = client.get(f"/projects/{project_id}/alerts", params={"limit": 1})
    assert len(limited.json()) == 1


def test_mark_alert_read_is_idempotent(client, seeded):
    """읽음 처리는 멱등이다. 두 번 눌러도 읽은 시각이 바뀌지 않는다."""
    project_id = seeded["project_id"]
    alert_id = client.get(f"/projects/{project_id}/alerts").json()[0]["id"]

    first = client.patch(f"/projects/{project_id}/alerts/{alert_id}/read")
    assert first.status_code == 200
    read_at = first.json()["read_at"]
    assert read_at is not None

    second = client.patch(f"/projects/{project_id}/alerts/{alert_id}/read")
    assert second.status_code == 200
    assert second.json()["read_at"] == read_at

    dashboard = client.get(f"/projects/{project_id}/dashboard").json()
    assert dashboard["unread_alert_count"] == 1


def test_mark_all_alerts_read(client, seeded):
    """모두 읽음은 남은 미읽음 수만큼 처리하고, 다시 부르면 0 건이다."""
    project_id = seeded["project_id"]

    first = client.patch(f"/projects/{project_id}/alerts/read-all")
    assert first.status_code == 200
    assert first.json()["updated"] == 2

    second = client.patch(f"/projects/{project_id}/alerts/read-all")
    assert second.json()["updated"] == 0

    dashboard = client.get(f"/projects/{project_id}/dashboard").json()
    assert dashboard["unread_alert_count"] == 0
    assert all(item["read_at"] is not None for item in dashboard["recent_alerts"])


def test_alert_read_is_scoped_to_project(client, db, seeded, tenants):
    """다른 조직 프로젝트의 알림은 읽음 처리할 수 없다(404)."""
    other_alert = Alert(
        project_id=tenants["project_b"].id,
        type=AlertType.DRIFT,
        message="B조직 알림",
    )
    db.add(other_alert)
    db.commit()

    # 내 프로젝트 경로에 남의 알림 ID 를 넣어도 찾지 못한다.
    mixed = client.patch(f"/projects/{seeded['project_id']}/alerts/{other_alert.id}/read")
    assert mixed.status_code == 404

    # 남의 프로젝트 경로는 프로젝트 단계에서 이미 404 다.
    direct = client.patch(f"/projects/{tenants['project_b'].id}/alerts/{other_alert.id}/read")
    assert direct.status_code == 404


def test_alerts_reject_reviewer(client, seeded):
    """심사원은 알림 API 에 접근할 수 없다."""
    login(client, "reviewer@example.com")
    assert client.get(f"/projects/{seeded['project_id']}/alerts").status_code == 403
    assert client.patch(f"/projects/{seeded['project_id']}/alerts/read-all").status_code == 403
