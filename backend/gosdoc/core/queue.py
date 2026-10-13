"""Очередь фоновых задач (dramatiq поверх RabbitMQ, ADR-0002).

API ставит задачу по имени актора и не импортирует workers: акторы живут в
образе воркера. Брокер создаётся лениво, соединение открывается при отправке.
"""

from collections.abc import Callable
from functools import lru_cache

import dramatiq
from dramatiq.brokers.rabbitmq import RabbitmqBroker

from gosdoc.core.config import get_settings

Enqueue = Callable[[str, str, int], None]


@lru_cache
def get_broker() -> RabbitmqBroker:
    return RabbitmqBroker(url=get_settings().rabbitmq_url)


def enqueue(queue: str, actor: str, job_id: int) -> None:
    message: dramatiq.Message[None] = dramatiq.Message(
        queue_name=queue, actor_name=actor, args=(job_id,), kwargs={}, options={}
    )
    get_broker().enqueue(message)


def get_enqueue() -> Enqueue:
    """Зависимость FastAPI; в тестах подменяется синхронным выполнением."""
    return enqueue
