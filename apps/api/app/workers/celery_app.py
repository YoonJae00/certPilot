"""Celery 애플리케이션.

브로커·백엔드 모두 Redis 를 쓴다(`REDIS_URL`).

실행:
    cd apps/api && uv run celery -A app.workers.celery_app:celery_app worker -l info

태스크는 항상 얇은 껍데기다. 실제 로직은 동기 함수(`run_ingest` 등)에 두고 태스크가
그걸 감싼다. 테스트는 Celery·Redis 없이 동기 함수를 직접 부른다.
"""

from celery import Celery

from app.core.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "certpilot",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
    include=["app.workers.ingest"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Seoul",
    enable_utc=True,
    task_track_started=True,
    # 워커가 죽어도 잡을 잃지 않는다.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)
