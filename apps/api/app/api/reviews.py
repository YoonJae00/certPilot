"""검수 워크플로 API (F6). Task 9 Worker가 채운다."""

from fastapi import APIRouter

router = APIRouter(prefix="/reviews", tags=["reviews"])
