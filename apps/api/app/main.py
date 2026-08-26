"""CertPilot API 진입점."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.api import (
    assessments,
    auth,
    connectors,
    dashboard,
    documents,
    drafts,
    orgs,
    projects,
    reviews,
)
from app.core.config import get_settings

app = FastAPI(
    title="CertPilot API",
    description="ISMS-P 준비·유지 코파일럿 백엔드",
    version="0.1.0",
)

_settings = get_settings()

# 브라우저 프런트(다른 포트)가 세션 쿠키를 실어 호출할 수 있어야 한다.
# `allow_origin_regex` 는 같은 네트워크의 다른 기기(http://192.168.x.x:3000 등)를 위한 것이며,
# 설정이 비어 있으면 None 이라 Starlette 기본값과 같아 정규식 허용이 꺼진다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _settings.web_origins.split(",") if o.strip()],
    allow_origin_regex=_settings.web_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(orgs.router)
app.include_router(projects.router)
app.include_router(documents.router)
app.include_router(assessments.router)
app.include_router(connectors.router)
app.include_router(drafts.router)
app.include_router(reviews.router)
app.include_router(dashboard.router)


class HealthResponse(BaseModel):
    """헬스 체크 응답."""

    status: str


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    """서비스 생존 확인. 외부 의존성(DB·Redis)은 확인하지 않는다."""
    return HealthResponse(status="ok")
