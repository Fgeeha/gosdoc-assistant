# 0008. LLM-клиент

**Контекст.** Какая модель будет в Inference/Sherpa, пока неизвестно; сервис
эмбеддингов может быть отдельным.

**Решение.** `gosdoc.core.llm.LLMClient` поверх httpx, OpenAI-совместимые
`/v1/chat/completions` и `/v1/embeddings`. Параметры: `LLM_BASE_URL`,
`LLM_MODEL`, `LLM_API_KEY`, `LLM_MAX_CONTEXT`, `EMBEDDINGS_BASE_URL`,
`EMBEDDINGS_MODEL`, `EMBEDDINGS_API_KEY`. Если `EMBEDDINGS_BASE_URL` не задан,
используется `LLM_BASE_URL`. TLS проверяется по `CA_BUNDLE`. Размер
фрагментов и число фрагментов в контексте RAG вычисляются из
`LLM_MAX_CONTEXT`. В dev и тестах работает `deploy/mock-llm` с
детерминированными ответами.

**Последствия.** Пакет `openai` не нужен. Смена модели — это смена
конфигурации; модель и версия промпта пишутся в результат задачи и в MLFlow.
