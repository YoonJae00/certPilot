"""헬스 엔드포인트 테스트. DB·Redis 없이 통과해야 한다."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    """/health 는 200과 {"status": "ok"} 를 돌려준다."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
