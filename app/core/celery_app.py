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
        "app.tasks.dispatch_tasks",
        "app.tasks.notification_tasks",
        "app.tasks.calcom_tasks",
        "app.tasks.booking_tasks",
        "app.tasks.inbox_poll_tasks",
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
    # Two queues, two dedicated workers (see run.bat).
    #
    # "heavy": the only tasks that touch the scraper/Chromium (website.analyze,
    # qualification.run). These are the ones whose RAM swings 200 MB-1.2 GB and
    # whose wall-clock runs minutes-to-an-hour, so this worker stays solo-pool
    # (concurrency 1) — never run two scrapes at once on a small instance.
    #
    # "light": everything else — beat-driven pollers/dispatch AND the fast,
    # single-LLM-call tasks (email.draft, icp.generate, scoring.run,
    # company_intelligence.generate) that never scrape. All of these are
    # seconds-long and RAM-cheap, so one thread-pool worker (concurrency 2-4,
    # see QUALIFY_LIGHT_WORKER_CONCURRENCY doc in run.bat) serves both without
    # a long qualification run ever blocking a "regenerate draft" click.
    task_default_queue="light",
    task_routes={
        "website.analyze": {"queue": "heavy"},
        "qualification.run": {"queue": "heavy"},
    },
    task_soft_time_limit=300,
    task_time_limit=360,
    # Outreach dispatch engine: 60s polling gives scheduled sends ~60s precision, well
    # under the 2-minute SCHEDULE_GAP slots dispatches are already spaced at (see
    # app/services/scheduling_service.py); the sweeper flags dispatches interrupted
    # mid-send (see app/tasks/dispatch_tasks.py).
    beat_schedule={
        "outreach-dispatch-due": {"task": "outreach.dispatch_due", "schedule": 60.0},
        # Interrupted-dispatch resolver: drafts stuck in SENDING for >10 min get
        # auto-verified against the Sent folder (see dispatch_tasks.sweep_stuck).
        "outreach-sweep-stuck": {"task": "outreach.sweep_stuck", "schedule": 1800.0},
        # Follow-up-due nudges (header bell). Hourly is plenty for day-granularity cadence.
        "outreach-follow-up-due": {"task": "outreach.raise_follow_up_due", "schedule": 3600.0},
        # Proactively refresh Cal.com tokens before they'd ever be seen expired by a
        # request; the lazy refresh in the request path is the real safety net.
        "calcom-refresh-expiring-tokens": {"task": "calcom.refresh_expiring_tokens", "schedule": 600.0},
        # Inbox reply poller — reads new mail, classifies intent, routes it
        # (app/services/inbox/). 10 minutes per your requirement.
        "inbox-poll-replies": {"task": "inbox.poll_replies", "schedule": 600.0},
        # Booking orchestrator safety net: re-processes PENDING bookings whose direct
        # enqueue was lost, and flags bookings stuck mid-claim for manual review.
        "booking-sweep-stale": {"task": "booking.sweep_stale", "schedule": 300.0},
    },
)

# Managed Redis over TLS (rediss:// — Upstash, Redis Cloud, Azure Cache) requires
# explicit certificate settings; Celery refuses to guess.
if settings.CELERY_BROKER_URL.startswith("rediss://"):
    celery_app.conf.broker_use_ssl = {"ssl_cert_reqs": ssl.CERT_REQUIRED}
if settings.CELERY_RESULT_BACKEND.startswith("rediss://"):
    celery_app.conf.redis_backend_use_ssl = {"ssl_cert_reqs": ssl.CERT_REQUIRED}
