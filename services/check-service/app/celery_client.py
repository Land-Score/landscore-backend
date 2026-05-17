from celery import Celery

from app.config import settings


celery_client = Celery("check-service-client", broker=settings.celery_broker_url)


def enqueue_check(payload: dict):
    return celery_client.send_task("run_check", args=[payload])

