# CertPilot API 이미지 (FastAPI + Celery 공용).
#
# **빌드 컨텍스트는 리포지토리 루트다.** `data/criteria/criteria.json` 과
# `data/rules/aws_rules.yaml` 은 코드가 `<리포 루트>/data/...` 로 찾기 때문에
# (`app/services/criteria_loader.py` 의 `parents[4]`), 이미지 안에서도 소스 트리와
# 같은 배치를 유지해야 한다:
#
#     /srv/certpilot/apps/api/app/...   ← 코드
#     /srv/certpilot/data/...           ← 인증기준·규칙 매핑
#
# 빌드:
#   docker build -f apps/api/Dockerfile -t certpilot-api .

# --------------------------------------------------------------------------
# 1단계: 의존성 설치 (uv 로 잠금 파일 그대로)
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

# uv 바이너리만 가져온다. 빌드 도구를 런타임 이미지에 남기지 않기 위함이다.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /srv/certpilot/apps/api

# 잠금 파일만 먼저 복사해 의존성 레이어를 캐시한다.
COPY apps/api/pyproject.toml apps/api/uv.lock ./
# 개발 의존성(pytest·ruff·mypy)은 운영 이미지에 넣지 않는다.
RUN uv sync --frozen --no-install-project --no-dev

# --------------------------------------------------------------------------
# 2단계: 런타임
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# curl 은 헬스체크용. 그 외 빌드 도구는 넣지 않는다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 루트로 돌리지 않는다(PRD §10).
RUN useradd --create-home --uid 10001 certpilot

ENV PATH="/srv/certpilot/apps/api/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv/certpilot/apps/api

COPY --from=builder /srv/certpilot/apps/api/.venv ./.venv
COPY apps/api/app ./app
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini ./alembic.ini
COPY apps/api/pyproject.toml ./pyproject.toml

# 인증기준·규칙 매핑·문서 템플릿. 코드가 리포 루트 기준으로 찾는 경로 그대로 둔다.
COPY data/criteria /srv/certpilot/data/criteria
COPY data/rules /srv/certpilot/data/rules
COPY data/templates /srv/certpilot/data/templates
COPY data/eval /srv/certpilot/data/eval

# 데모 시드용 샘플 문서. 운영 데이터가 아니라 가상 문서다(부록 D).
COPY data/samples /srv/certpilot/data/samples
COPY scripts /srv/certpilot/scripts

RUN chown -R certpilot:certpilot /srv/certpilot
USER certpilot

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# 워커·비트는 compose 에서 command 를 덮어쓴다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
