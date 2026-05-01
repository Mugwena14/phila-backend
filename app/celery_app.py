from celery import Celery
from app.core.config import settings
from celery.schedules import crontab

celery_app = Celery(
    "phila",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.whatsapp_tasks",
        "app.tasks.health_tasks",
    ],
)




celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Africa/Johannesburg",
    enable_utc=True,
    task_max_retries=3,
    task_default_retry_delay=60,

    # ── Scheduled jobs ──────────────────────────────────────────────
    beat_schedule={
        # Health memory + care gap scan — every Sunday at 08:00 SAST
        "weekly-health-scan": {
            "task": "app.tasks.health_tasks.run_weekly_health_scan",
            "schedule": crontab(hour=8, minute=0, day_of_week=0),
        },
        # Prescription refill check — every day at 09:00 SAST
        "daily-prescription-check": {
            "task": "app.tasks.health_tasks.run_prescription_refill_check",
            "schedule": crontab(hour=9, minute=0),
        },
    },
)