# ГосДок Ассистент

Помощник для сотрудников государственной службы: проверка документов по
нормативной базе, подготовка служебных писем, перенос данных между таблицами.
Работает полностью в закрытом контуре: модель, поиск и хранилище — локальные,
обращений к внешним адресам нет.

Все результаты, подготовленные автоматически, — черновики и требуют проверки
сотрудником.

## Состояние

| Этап | Содержание | Статус |
|---|---|---|
| 1 | Архитектура, ADR | готово — [docs/architecture.md](docs/architecture.md), [docs/adr](docs/adr) |
| 2 | Каркас: вход, роли, аудит, хранилище файлов, docker-compose, CI | готово |
| 3 | Модуль «Перенос данных между таблицами» | — |
| 4 | Модуль «Подготовка писем» | — |
| 5 | Модуль «Проверка документов» | — |
| 6 | Helm, DAG Airflow, мониторинг | — |
| 7 | Документация администратора и пользователя | — |

## Быстрый старт

Нужны Docker с Compose v2 и `make`.

```sh
make up-local        # при первом запуске создаст .env из .env.example
```

Откройте http://localhost:8080. Логин `admin`, пароль — `GOSDOC_ADMIN_PASSWORD`
из `.env`; при первом входе его нужно сменить.

Поднимаются PostgreSQL, Redis, RabbitMQ, Elasticsearch, ChromaDB, SeaweedFS
(S3), заглушка модели и само приложение. Сервисы находятся во внутренней сети
Docker без выхода наружу; опубликован только порт веб-интерфейса.

## Разработка

```sh
make install         # зависимости (uv, npm)
make check           # ruff, mypy, tsc и все тесты
make test-unit       # быстрые тесты без Docker
make help            # все цели
```

Интеграционные тесты поднимают PostgreSQL и SeaweedFS через testcontainers.
Тесты не могут ходить во внешнюю сеть (`pytest-socket`): разрешены только
локальные адреса.

## Структура

```
backend/gosdoc/   API и доменная логика (core — общее ядро; модули — по этапам)
backend/alembic/  миграции БД
workers/          фоновые задачи dramatiq
frontend/         React + TypeScript + Ant Design
rag/              индексация НПА и поиск (этап 5)
airflow/dags/     регламентные задачи (этап 6)
deploy/           Dockerfile, docker-compose.dev.yml, mock-llm, Helm (этап 6)
templates/        бланки писем, шаблоны маппинга
docs/             архитектура, ADR, инструкции
tests/            unit, integration, fixtures
```

## Конфигурация

Все параметры задаются переменными окружения, полный список с описанием — в
`backend/gosdoc/core/config.py`. Основные:

| Переменная | Назначение |
|---|---|
| `DATABASE_URL`, `REDIS_URL`, `RABBITMQ_URL` | подключение к сервисам |
| `STORAGE_BACKEND` | `s3` (SeaweedFS и др.), `postgres` или `local` |
| `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` | S3-хранилище |
| `FILE_RETENTION_DAYS` | срок хранения загруженных файлов (30) |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_MAX_CONTEXT` | модель (OpenAI-совместимый API) |
| `EMBEDDINGS_BASE_URL`, `EMBEDDINGS_MODEL` | эмбеддинги, если сервис отдельный |
| `AUTH_PROVIDER` | `local` или `ldap` |
| `CA_BUNDLE` | корневой сертификат для TLS к сервисам `*.fgau.local` |
