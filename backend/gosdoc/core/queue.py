"""Очередь фоновых задач (dramatiq поверх RabbitMQ, ADR-0002).

API ставит задачу по имени актора и не импортирует workers: акторы живут в
образе воркера. Брокер создаётся лениво, соединение открывается при отправке.
"""

from collections.abc import Callable, Iterable
from functools import lru_cache

import dramatiq
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from sqlalchemy import event
from sqlalchemy.orm import Session

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


def enqueue_after_commit(
    db: Session, enqueue: Enqueue, queue: str, actor: str, ids: Iterable[int]
) -> None:
    """Воркер должен увидеть записи в БД, поэтому сообщения уходят после коммита."""
    pending = list(ids)

    def send(_: Session) -> None:
        for item_id in pending:
            enqueue(queue, actor, item_id)

    event.listen(db, "after_commit", send, once=True)
