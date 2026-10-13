# 0009. Развёртывание

**Контекст.** Все внешние сервисы заказываются на платформе ФГАУ «ЦИТ»: ВМ или
под со статическим IP, внутренний DNS `*.fgau.local`, локальный корневой CA.

**Решение.**

- Helm-чарт разворачивает только приложение: api, воркеры, frontend/nginx,
  Job миграций Alembic. Адреса и пароли внешних сервисов — в values/Secret.
- CA-бандл монтируется из Secret/ConfigMap, путь передаётся в `CA_BUNDLE` и
  используется для TLS к PostgreSQL, S3, Inference и LDAP.
- liveness — `/health/live`, readiness — `/health/ready`. Readiness опрашивает
  PostgreSQL, Redis, RabbitMQ, хранилище, Elasticsearch, векторное хранилище и
  LLM (с таймаутом `DEPS_CHECK_TIMEOUT_S`), но 503 отдаёт только при отказе
  PostgreSQL или Redis: без них не работают вход и сессии. Отказ остальных —
  деградация: под остаётся в балансировке, затронутый модуль возвращает
  понятную ошибку, состояние видно в ответе readiness и в мониторинге.
  initContainer выполняет `gosdoc wait-deps` и не даёт поду стартовать, пока
  зависимости недоступны.
- DAG лежат в `airflow/dags` и выкладываются в заказанный Airflow; в чарт не
  входят. SeaweedFS при необходимости ставится официальным чартом отдельно.
- `deploy/docker-compose.dev.yml` поднимает всё сам: PostgreSQL, Redis,
  RabbitMQ, Elasticsearch, ChromaDB, SeaweedFS, mock-LLM.

**Последствия.** Наш чарт не владеет данными; обновление приложения не
затрагивает БД, кроме миграций.
