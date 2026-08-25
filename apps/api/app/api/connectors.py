"""증적 커넥터 API (F5). Task 7 Worker가 채운다."""

from fastapi import APIRouter

router = APIRouter(prefix="/projects/{project_id}/connectors", tags=["connectors"])
