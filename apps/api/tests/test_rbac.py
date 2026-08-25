"""PRD §3 권한 매트릭스 테스트."""

from tests.conftest import login

PROJECT_PAYLOAD = {
    "name": "데모핀테크",
    "cert_type": "ISMS-P",
    "is_simplified": True,
    "scope_text": "결제 서비스 전체",
    "audit_due_date": "2027-06-30",
}


def test_anonymous_cannot_list_projects(client, tenants):
    """비로그인은 401 이다."""
    assert client.get("/projects").status_code == 401


def test_org_admin_can_create_project(client, tenants):
    """org_admin 은 자기 조직 프로젝트를 만든다."""
    login(client, tenants["admin_a"].email)

    response = client.post("/projects", json=PROJECT_PAYLOAD)

    assert response.status_code == 201
    assert response.json()["org_id"] == str(tenants["org_a"].id)


def test_org_member_cannot_create_project(client, tenants):
    """org_member 는 프로젝트를 만들 수 없다(403)."""
    login(client, tenants["member_a"].email)

    assert client.post("/projects", json=PROJECT_PAYLOAD).status_code == 403


def test_org_member_can_read_projects(client, tenants):
    """org_member 는 열람은 된다."""
    login(client, tenants["member_a"].email)

    response = client.get("/projects")

    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [str(tenants["project_a"].id)]


def test_org_member_cannot_update_project(client, tenants):
    """org_member 는 수정할 수 없다."""
    login(client, tenants["member_a"].email)

    response = client.patch(f"/projects/{tenants['project_a'].id}", json={"name": "변경"})

    assert response.status_code == 403


def test_org_admin_can_update_own_project(client, tenants):
    """org_admin 은 자기 조직 프로젝트를 수정한다."""
    login(client, tenants["admin_a"].email)

    response = client.patch(
        f"/projects/{tenants['project_a'].id}", json={"name": "이름 변경", "is_simplified": False}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "이름 변경"
    assert response.json()["is_simplified"] is False


def test_reviewer_cannot_list_org_projects(client, tenants):
    """reviewer 는 조직 스코프 API 를 쓸 수 없다(검수 과제 경유만 허용)."""
    login(client, tenants["reviewer"].email)

    assert client.get("/projects").status_code == 403
    assert client.get(f"/projects/{tenants['project_a'].id}").status_code == 403
    assert client.get(f"/orgs/{tenants['org_a'].id}").status_code == 403


def test_operator_can_read_any_org(client, tenants):
    """operator 는 조직을 지정해 타 조직도 열람한다."""
    login(client, tenants["operator"].email)

    listed = client.get("/projects", params={"org_id": str(tenants["org_b"].id)})
    single = client.get(f"/projects/{tenants['project_b'].id}")

    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [str(tenants["project_b"].id)]
    assert single.status_code == 200
    assert single.json()["org_id"] == str(tenants["org_b"].id)


def test_operator_must_specify_org_for_listing(client, tenants):
    """운영자가 org_id 를 빼면 400 이다(전체 조회를 실수로 열지 않는다)."""
    login(client, tenants["operator"].email)

    assert client.get("/projects").status_code == 400


def test_operator_cannot_update_project(client, tenants):
    """operator 는 열람 전용이다. 수정은 org_admin 만 한다."""
    login(client, tenants["operator"].email)

    response = client.patch(f"/projects/{tenants['project_b'].id}", json={"name": "운영자 수정"})

    assert response.status_code == 403


def test_only_operator_creates_orgs_and_users(client, tenants):
    """조직·사용자 생성은 운영자 전용이다."""
    login(client, tenants["admin_a"].email)
    assert client.post("/orgs", json={"name": "몰래조직"}).status_code == 403
    assert client.get("/orgs").status_code == 403
    assert (
        client.post(
            f"/orgs/{tenants['org_a'].id}/users",
            json={"email": "new@example.com", "password": "fixture-password-1234"},
        ).status_code
        == 403
    )

    client.post("/auth/logout")
    login(client, tenants["operator"].email)
    created = client.post("/orgs", json={"name": "새조직", "plan": "standard"})
    assert created.status_code == 201

    created_user = client.post(
        f"/orgs/{created.json()['id']}/users",
        json={
            "email": "new-admin@example.com",
            "password": "fixture-password-1234",
            "role": "org_admin",
        },
    )
    assert created_user.status_code == 201
    assert created_user.json()["org_id"] == created.json()["id"]
    assert "password" not in created_user.json()


def test_duplicate_email_returns_conflict(client, tenants):
    """이미 있는 이메일이면 409 다."""
    login(client, tenants["operator"].email)

    response = client.post(
        f"/orgs/{tenants['org_a'].id}/users",
        json={"email": tenants["member_a"].email, "password": "fixture-password-1234"},
    )

    assert response.status_code == 409


def test_reviewer_role_cannot_be_created_in_org(client, tenants):
    """조직 소속 사용자는 reviewer/operator 역할을 가질 수 없다."""
    login(client, tenants["operator"].email)

    response = client.post(
        f"/orgs/{tenants['org_a'].id}/users",
        json={
            "email": "sneaky@example.com",
            "password": "fixture-password-1234",
            "role": "operator",
        },
    )

    assert response.status_code == 422


def test_org_admin_can_read_own_org(client, tenants):
    """org_admin 은 자기 조직 정보를 볼 수 있다."""
    login(client, tenants["admin_a"].email)

    response = client.get(f"/orgs/{tenants['org_a'].id}")

    assert response.status_code == 200
    assert response.json()["name"] == "A조직"
