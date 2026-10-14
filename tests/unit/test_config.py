import pytest
from gosdoc.core.config import Settings
from pydantic import ValidationError


def test_prod_refuses_dev_defaults() -> None:
    with pytest.raises(ValidationError, match=r"DATABASE_URL.*S3_SECRET_KEY"):
        Settings(
            environment="prod",
            database_url="postgresql+psycopg://gosdoc:gosdoc@db:5432/gosdoc",
            s3_secret_key="gosdoc",
        )


def test_prod_accepts_real_values() -> None:
    s = Settings(
        environment="prod",
        database_url="postgresql+psycopg://app:s3cret-long-password@db:5432/gosdoc",
        rabbitmq_url="amqps://app:s3cret-long-password@mq:5671/",
        s3_secret_key="s3cret-long-password",
        session_cookie_secure=True,
    )
    assert s.environment == "prod"


def test_ldap_requires_addresses() -> None:
    with pytest.raises(ValidationError, match="LDAP_URL"):
        Settings(auth_provider="ldap")
