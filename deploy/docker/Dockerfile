# syntax=docker/dockerfile:1
# Базовый образ — только из отечественных реестров (ADR-0007):
#   Astra Linux (по умолчанию): registry.astralinux.ru/astra/ubi18
#   РЕД ОС:                     registry.red-soft.ru/ubi8/ubi
# python:3.11-slim передаётся только из docker-compose.dev.yml.
ARG BASE_IMAGE=registry.astralinux.ru/astra/ubi18

# --- Python и зависимости ---------------------------------------------------
FROM ${BASE_IMAGE} AS python-base
COPY --chmod=755 deploy/docker/os-packages.sh /usr/local/bin/os-packages.sh
RUN os-packages.sh python
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/app/.venv/bin:$PATH

FROM python-base AS builder
# uv ставится из зеркала PyPI в контуре (PIP_INDEX_URL), а не из чужого реестра образов.
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV UV_INDEX_URL=${PIP_INDEX_URL} UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never UV_PYTHON=python3
RUN python3 -m venv /opt/uv && /opt/uv/bin/pip install --index-url "${PIP_INDEX_URL}" uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# uv.lock хранит прямые ссылки на files.pythonhosted.org, и `uv sync` качал бы по
# ним. Поэтому версии и хеши берём из lock, а пакеты — из зеркала PIP_INDEX_URL.
RUN /opt/uv/bin/uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
    && /opt/uv/bin/uv venv /app/.venv \
    && /opt/uv/bin/uv pip install --python /app/.venv/bin/python --require-hashes \
       -r /tmp/requirements.txt
COPY backend backend
COPY workers workers
COPY rag rag
COPY alembic.ini README.md ./
RUN /opt/uv/bin/uv pip install --python /app/.venv/bin/python --no-deps -e .

# --- API --------------------------------------------------------------------
FROM python-base AS api
RUN useradd --system --uid 10001 --home-dir /app gosdoc
WORKDIR /app
COPY --from=builder --chown=10001 /app /app
USER 10001
EXPOSE 8000
# Адрес клиента берётся из X-Forwarded-For только от доверенного прокси:
# FORWARDED_ALLOW_IPS задаётся при развёртывании (адрес/подсеть nginx).
CMD ["uvicorn", "gosdoc.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

# --- Воркер: + Tesseract (rus) и LibreOffice --------------------------------
# Пакеты ОС ставятся до копирования кода, чтобы тяжёлый слой не пересобирался.
FROM python-base AS worker
RUN os-packages.sh worker && useradd --system --uid 10001 --home-dir /app gosdoc
WORKDIR /app
COPY --from=builder --chown=10001 /app /app
USER 10001
CMD ["dramatiq", "workers.actors", "--processes", "2", "--threads", "4"]

# --- Фронтенд: сборка Node.js из пакетов ОС, раздача nginx из пакетов ОС ---
FROM ${BASE_IMAGE} AS frontend-build
COPY --chmod=755 deploy/docker/os-packages.sh /usr/local/bin/os-packages.sh
RUN os-packages.sh node
ARG NPM_REGISTRY=https://registry.npmjs.org/
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --registry "${NPM_REGISTRY}" --no-audit --no-fund
COPY frontend ./
RUN npm run build

FROM ${BASE_IMAGE} AS frontend
COPY --chmod=755 deploy/docker/os-packages.sh /usr/local/bin/os-packages.sh
RUN os-packages.sh nginx && rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf
COPY deploy/docker/nginx.conf /etc/nginx/nginx.conf
COPY --from=frontend-build /src/dist /usr/share/gosdoc
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
