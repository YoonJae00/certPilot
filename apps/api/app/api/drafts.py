"""문서 코파일럿 API (F4). Task 8 Worker가 채운다."""

from fastapi import APIRouter

router = APIRouter(prefix="/projects/{project_id}/drafts", tags=["drafts"])
