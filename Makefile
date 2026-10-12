# Единая точка входа для всех операций проекта.
# Python-зависимости — только через uv, фронтенд — npm.

.DEFAULT_GOAL := help
COMPOSE ?= docker compose
DC_LOCAL := $(COMPOSE) --env-file .env -f deploy/docker-compose.dev.yml

# Базовый образ для прод-сборки (ADR-0007):
#   Astra Linux — registry.astralinux.ru/astra/ubi18 (по умолчанию)
#   РЕД ОС      — registry.red-soft.ru/ubi8/ubi
BASE_IMAGE ?= registry.astralinux.ru/astra/ubi18
PIP_INDEX_URL ?= https://pypi.org/simple
NPM_REGISTRY ?= https://registry.npmjs.org/
IMAGE_PREFIX ?= gosdoc
IMAGE_TAG ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)

.PHONY: help install run run-worker run-mock-llm run-frontend lint format test test-unit \
        check precommit migrate migration up-local down-local logs-local images clean

help: ## Показать список целей
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Установка ---------------------------------------------------------------

install: ## Установить зависимости backend и frontend
	uv sync
	cd frontend && npm ci --no-audit --no-fund

# --- Запуск без Docker (зависимости — из up-local или свои) ------------------

run: ## Запустить API локально с автоперезагрузкой
	uv run uvicorn gosdoc.main:app --reload --app-dir backend

run-worker: ## Запустить воркер dramatiq
	uv run dramatiq workers.actors

run-mock-llm: ## Запустить заглушку LLM на :8090
	uv run uvicorn app:app --app-dir deploy/mock-llm --port 8090

run-frontend: ## Запустить Vite dev server
	cd frontend && npm run dev

# --- Проверка ----------------------------------------------------------------

lint: ## Проверить код (ruff, mypy, tsc)
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	cd frontend && npm run typecheck

format: ## Отформатировать код и починить импорты
	uv run ruff check --fix .
	uv run ruff format .

test-unit: ## Unit-тесты (без Docker)
	uv run pytest tests/unit

test: ## Все тесты (интеграционные поднимают контейнеры через testcontainers)
	uv run pytest

check: lint test ## Полная проверка перед коммитом

# --- База данных -------------------------------------------------------------

migrate: ## Применить миграции к DATABASE_URL
	uv run alembic upgrade head

migration: ## Создать миграцию: make migration m="описание"
	uv run alembic revision --autogenerate -m "$(m)"

# --- Docker ------------------------------------------------------------------
# --env-file .env обязателен: без него Compose ищет .env рядом с compose-файлом.

up-local: ## Поднять всё локально одной командой (http://localhost:8080)
	@test -f .env || { cp .env.example .env; echo "Создан .env из .env.example — смените пароли"; }
	$(DC_LOCAL) up -d --build --wait

down-local: ## Остановить локальное окружение
	$(DC_LOCAL) down

logs-local: ## Логи локального окружения
	$(DC_LOCAL) logs -f --tail=100

images: ## Собрать прод-образы (BASE_IMAGE=... для РЕД ОС)
	for t in api worker frontend; do \
	  docker build -f deploy/docker/Dockerfile --target $$t \
	    --build-arg BASE_IMAGE=$(BASE_IMAGE) --build-arg PIP_INDEX_URL=$(PIP_INDEX_URL) \
	    --build-arg NPM_REGISTRY=$(NPM_REGISTRY) \
	    -t $(IMAGE_PREFIX)-$$t:$(IMAGE_TAG) . || exit 1; \
	done

# --- Обслуживание ------------------------------------------------------------

clean: ## Удалить кэши и артефакты сборки
	rm -rf .pytest_cache .mypy_cache .ruff_cache frontend/dist var
	find . -name __pycache__ -type d -prune -not -path './.venv/*' -exec rm -rf {} +
