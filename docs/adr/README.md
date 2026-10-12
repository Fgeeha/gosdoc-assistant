# Architecture Decision Records

Формат: контекст → решение → последствия. Принятое решение не правится —
при пересмотре заводится новый ADR со ссылкой на старый.

| № | Решение | Статус |
|---|---|---|
| [0001](0001-modular-monolith.md) | Модульный монолит, синхронный стек | принято |
| [0002](0002-dramatiq.md) | Фоновые задачи на dramatiq поверх RabbitMQ | принято |
| [0003](0003-own-rag-layer.md) | Собственный RAG-слой без LangChain/LlamaIndex | принято |
| [0004](0004-file-storage.md) | FileStorage: s3 (SeaweedFS) / postgres / local | принято |
| [0005](0005-authentication.md) | Свои учётные записи + LDAP через AuthProvider | принято |
| [0006](0006-multitenancy.md) | Мультиарендность через organization_id | принято |
| [0007](0007-base-images.md) | Базовые образы Astra Linux / РЕД ОС | принято |
| [0008](0008-llm-client.md) | OpenAI-совместимый клиент, параметры из конфигурации | принято |
| [0009](0009-deployment.md) | Helm-чарт только для приложения | принято |
| [0010](0010-docx-review-mvp.md) | В MVP рецензирование DOCX только примечаниями | принято |
