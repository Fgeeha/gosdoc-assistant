"""Фоновые задачи dramatiq. Запуск: dramatiq workers.actors"""

import dramatiq
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from gosdoc.core.config import get_settings
from gosdoc.core.db import get_sessionmaker
from gosdoc.core.files import cleanup_expired_files
from gosdoc.core.logging import setup_logging
from gosdoc.core.storage import get_storage

_settings = get_settings()
setup_logging(_settings.log_level)
dramatiq.set_broker(RabbitmqBroker(url=_settings.rabbitmq_url))


@dramatiq.actor(queue_name="maintenance", max_retries=3)
def cleanup_files() -> None:
    cleanup_expired_files(get_sessionmaker(), get_storage())
