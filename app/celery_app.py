from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "phila",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.whatsapp_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Africa/Johannesburg",
    enable_utc=True,
    # Retry failed tasks up to 3 times
    task_max_retries=3,
    task_default_retry_delay=60,
)