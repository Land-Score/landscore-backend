from celery import Celery

from app.config import settings


celery_client = Celery("search-service-client", broker=settings.celery_broker_url)


def enqueue_search(payload: dict):
    return celery_client.send_task("run_search", args=[payload])

