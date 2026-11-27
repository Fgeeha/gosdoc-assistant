"""Брокер задач проверяет сертификат RabbitMQ по CA_BUNDLE, как и health-проверка."""

import ssl

import pika
import pytest
from gosdoc.core import queue
from gosdoc.core.config import Settings


def test_no_tls_options_without_amqps_or_ca() -> None:
    assert queue.amqp_ssl_options(Settings(rabbitmq_url="amqp://u:p@mq:5672/")) is None
    assert queue.amqp_ssl_options(Settings(rabbitmq_url="amqps://u:p@mq:5671/")) is None


def test_tls_options_from_ca_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[str | None] = []
    context = ssl.create_default_context()

    def fake_context(*, cafile: str | None = None) -> ssl.SSLContext:
        loaded.append(cafile)
        return context

    monkeypatch.setattr(queue.ssl, "create_default_context", fake_context)
    s = Settings(rabbitmq_url="amqps://u:p@mq:5671/", ca_bundle="/etc/gosdoc/ca/ca.pem")
    tls = queue.amqp_ssl_options(s)
    assert tls is not None and tls.context is context
    assert loaded == ["/etc/gosdoc/ca/ca.pem"]


@pytest.mark.parametrize("url", ["amqps://u:p@mq:5671/", "amqps://u:p@a:5671/;amqps://u:p@b:5671/"])
def test_broker_uses_tls_options(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setattr(queue, "get_settings", lambda: Settings(rabbitmq_url=url))
    tls = pika.SSLOptions(ssl.create_default_context())
    monkeypatch.setattr(queue, "amqp_ssl_options", lambda _: tls)
    params = queue.get_broker.__wrapped__().parameters
    assert all(p.ssl_options is tls for p in (params if isinstance(params, list) else [params]))


def test_health_check_tries_every_node(monkeypatch: pytest.MonkeyPatch) -> None:
    from gosdoc.core import health

    hosts: list[str] = []

    class Connection:
        def __init__(self, nodes: list[pika.URLParameters]) -> None:
            hosts.extend(n.host for n in nodes)

        def close(self) -> None: ...

    monkeypatch.setattr(health.pika, "BlockingConnection", Connection)
    health._checks(Settings(rabbitmq_url="amqp://u:p@a:5672/;amqp://u:p@b:5672/"))["rabbitmq"]()
    assert hosts == ["a", "b"]
