"""CertPilot API 진입점."""

from fastapi import FastAPI
from pydantic import BaseModel

from app.api import assessments, auth, documents, orgs, projects

app = FastAPI(
    title="CertPilot API",
    description="ISMS-P 준비·유지 코파일럿 백엔드",
    version="0.1.0",
)

app.include_router(auth.router)
app.include_router(orgs.router)
app.include_router(projects.router)
app.include_router(documents.router)
app.include_router(assessments.router)


class HealthResponse(BaseModel):
    """헬스 체크 응답."""

    status: str


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    """서비스 생존 확인. 외부 의존성(DB·Redis)은 확인하지 않는다."""
    return HealthResponse(status="ok")
