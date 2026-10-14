"""Конфигурация приложения: только переменные окружения (и .env в разработке)."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Значения из dev-окружения (config и docker-compose.dev.yml) — в проде запрещены.
_DEV_S3_SECRETS = frozenset({"gosdoc", "gosdoc-dev-secret"})


class Settings(BaseSettings):
    """Все параметры подключения и поведения. Значения по умолчанию — для dev."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    # Локальный корневой CA (*.fgau.local): TLS к PostgreSQL задаётся в DATABASE_URL
    # (sslrootcert=...), к S3, Inference и LDAP — через этот путь.
    ca_bundle: str | None = None

    database_url: str = "postgresql+psycopg://gosdoc:gosdoc@localhost:5432/gosdoc"
    redis_url: str = "redis://localhost:6379/0"
    rabbitmq_url: str = "amqp://gosdoc:gosdoc@localhost:5672/"
    elasticsearch_url: str = "http://localhost:9200"
    vector_store: Literal["chroma", "milvus"] = "chroma"
    vector_store_url: str = "http://localhost:8001"

    storage_backend: Literal["s3", "postgres", "local"] = "s3"
    storage_max_file_mb: int = 50
    storage_local_path: str = "./var/files"
    s3_endpoint_url: str = "http://localhost:8333"
    s3_access_key: str = "gosdoc"
    s3_secret_key: str = "gosdoc"  # noqa: S105 — dev-значение, в проде из Secret
    s3_bucket: str = "gosdoc"
    s3_region: str = "us-east-1"
    file_retention_days: int = 30

    llm_base_url: str = "http://localhost:8090/v1"
    llm_model: str = "mock"
    llm_api_key: str = "none"
    llm_max_context: int = 8192
    llm_timeout_s: float = 120.0
    embeddings_base_url: str | None = None  # None — тот же сервис, что и LLM
    embeddings_model: str = "mock-embeddings"
    embeddings_api_key: str | None = None

    auth_provider: Literal["local", "ldap"] = "local"
    auth_max_failed_attempts: int = 5
    auth_lockout_minutes: int = 15
    auth_min_password_length: int = 12
    session_ttl_minutes: int = 480
    session_cookie_secure: bool = False

    # Адреса каталога — только из окружения, например LDAP_URL=ldaps://dc.<домен>.
    ldap_url: str = ""
    ldap_user_dn_template: str = ""  # например {login}@<домен>
    ldap_search_base: str = ""
    ldap_user_filter: str = "(sAMAccountName={login})"
    # Группа AD (DN или CN) → роль приложения, JSON: {"CN=GosDoc-Admins,...": "sys_admin"}.
    ldap_group_roles: dict[str, str] = Field(default_factory=dict)

    default_organization_id: int = 1
    deps_check_timeout_s: float = 3.0

    @model_validator(mode="after")
    def _check_prod(self) -> Self:
        """В проде не стартуем на dev-паролях и с незаполненным LDAP."""
        if self.auth_provider == "ldap" and not (
            self.ldap_url and self.ldap_user_dn_template and self.ldap_search_base
        ):
            raise ValueError(
                "Для AUTH_PROVIDER=ldap нужны LDAP_URL, LDAP_USER_DN_TEMPLATE, LDAP_SEARCH_BASE"
            )
        if self.environment != "prod":
            return self
        dev_values = {
            "DATABASE_URL": ":gosdoc@" in self.database_url,
            "RABBITMQ_URL": ":gosdoc@" in self.rabbitmq_url,
            "S3_SECRET_KEY": self.storage_backend == "s3" and self.s3_secret_key in _DEV_S3_SECRETS,
            "SESSION_COOKIE_SECURE": not self.session_cookie_secure,
        }
        bad = [name for name, is_dev in dev_values.items() if is_dev]
        if bad:
            raise ValueError(f"ENVIRONMENT=prod с dev-значениями: {', '.join(bad)}")
        return self


@lru_cache
def get_settings() -> Settings:
    """Единый экземпляр настроек на процесс."""
    return Settings()
