"""유지 대시보드·알림 API (F8). Task 10 Worker가 채운다."""

from fastapi import APIRouter

router = APIRouter(prefix="/projects/{project_id}", tags=["dashboard"])
