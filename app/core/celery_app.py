import ssl

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "focalreach",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.website_tasks",
        "app.tasks.company_intelligence_tasks",
        "app.tasks.icp_tasks",
        "app.tasks.qualification_tasks",
        "app.tasks.scoring_tasks",
        "app.tasks.email_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=300,
    task_time_limit=360,
)

# Managed Redis over TLS (rediss:// — Upstash, Redis Cloud, Azure Cache) requires
# explicit certificate settings; Celery refuses to guess.
if settings.CELERY_BROKER_URL.startswith("rediss://"):
    celery_app.conf.broker_use_ssl = {"ssl_cert_reqs": ssl.CERT_REQUIRED}
if settings.CELERY_RESULT_BACKEND.startswith("rediss://"):
    celery_app.conf.redis_backend_use_ssl = {"ssl_cert_reqs": ssl.CERT_REQUIRED}
