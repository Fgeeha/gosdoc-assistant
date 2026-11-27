"""Очередь фоновых задач (dramatiq поверх RabbitMQ, ADR-0002).

API ставит задачу по имени актора и не импортирует workers: акторы живут в
образе воркера. Брокер создаётся лениво, соединение открывается при отправке.
"""

import ssl
from collections.abc import Callable, Iterable
from functools import lru_cache

import dramatiq
import pika
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from sqlalchemy import event
from sqlalchemy.orm import Session

from gosdoc.core.config import Settings, get_settings

Enqueue = Callable[[str, str, int], None]


def amqp_ssl_options(s: Settings) -> pika.SSLOptions | None:
    """TLS по CA_BUNDLE; без него pika сверяет сертификат с системными CA образа."""
    if s.rabbitmq_url.startswith("amqps") and s.ca_bundle:
        return pika.SSLOptions(ssl.create_default_context(cafile=s.ca_bundle))
    return None


@lru_cache
def get_broker() -> RabbitmqBroker:
    s = get_settings()
    broker = RabbitmqBroker(url=s.rabbitmq_url)
    if (tls := amqp_ssl_options(s)) is not None:
        # Несколько узлов через «;» в URL dramatiq разбирает в список параметров.
        params = broker.parameters
        for p in params if isinstance(params, list) else [params]:
            p.ssl_options = tls
    return broker


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
