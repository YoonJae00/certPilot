"""크로스 테넌트 격리 테스트(PRD §10).

A조직 관리자가 B조직 데이터에 어떤 경로로도 닿지 못하는지 확인한다.
"""

from sqlalchemy import select

from app.models import Project
from tests.conftest import login


def test_cross_tenant_project_read_is_404(client, tenants):
    """A조직 admin 이 B조직 프로젝트를 직접 조회하면 404 다(존재를 흘리지 않는다)."""
    login(client, tenants["admin_a"].email)

    response = client.get(f"/projects/{tenants['project_b'].id}")

    assert response.status_code == 404
    assert "B프로젝트" not in response.text


def test_cross_tenant_write_and_org_read_are_blocked(client, tenants, db):
    """A조직 admin 은 B조직 데이터를 수정하거나 조직 정보를 읽을 수 없다."""
    login(client, tenants["admin_a"].email)

    updated = client.patch(f"/projects/{tenants['project_b'].id}", json={"name": "탈취"})
    org_read = client.get(f"/orgs/{tenants['org_b'].id}")
    forced_scope = client.get("/projects", params={"org_id": str(tenants["org_b"].id)})

    assert updated.status_code == 404
    assert org_read.status_code == 404
    assert forced_scope.status_code == 404

    # DB 원본이 그대로인지 확인한다.
    project_b = db.execute(
        select(Project).where(Project.id == tenants["project_b"].id)
    ).scalar_one()
    assert project_b.name == "B프로젝트"


def test_list_endpoint_never_mixes_other_org_rows(client, tenants):
    """목록 API 에 다른 조직 데이터가 섞이지 않는다."""
    login(client, tenants["admin_a"].email)
    client.post(
        "/projects",
        json={"name": "A추가프로젝트", "cert_type": "ISMS", "is_simplified": False},
    )

    response = client.get("/projects")

    assert response.status_code == 200
    rows = response.json()
    assert {row["org_id"] for row in rows} == {str(tenants["org_a"].id)}
    assert str(tenants["project_b"].id) not in {row["id"] for row in rows}
    assert "B프로젝트" not in response.text


def test_project_create_ignores_client_supplied_org_id(client, tenants, db):
    """클라이언트가 org_id 를 보내도 서버는 세션의 조직만 쓴다."""
    login(client, tenants["admin_a"].email)

    response = client.post(
        "/projects",
        json={
            "name": "주입시도",
            "cert_type": "ISMS-P",
            "org_id": str(tenants["org_b"].id),
        },
    )

    assert response.status_code == 201
    assert response.json()["org_id"] == str(tenants["org_a"].id)
