# Мониторинг

Все компоненты мониторинга — заказанные сервисы платформы; приложение и чарт
только поставляют данные.

| Что | Куда | Как |
|---|---|---|
| Логи api, воркеров, nginx | Loki | JSON в stdout, сбор агентом кластера — `promtail-pipeline.yaml`. ПДн маскируются в приложении. |
| Версии промптов и моделей | MLFlow | DAG `gosdoc_quality` → `POST /api/internal/mlflow/sync-prompts`: эксперимент `gosdoc-prompts`, run на версию промпта × модель. В `jobs.result` и версиях писем пишутся `prompt_version` и `model_name`. |
| Качество ответов модели и дрейф | Evidently UI | DAG `gosdoc_quality` берёт обезличенную статистику замечаний (`/api/internal/quality/findings`: вид, критичность, оценка сотрудника, отброшенные ответы без цитаты, версия промпта и модели) и отправляет отчёт в проект Evidently. Тексты документов и цитаты не выгружаются. |
| Доступность зависимостей | readiness | `/health/ready` показывает состояние всех 7 зависимостей; 503 — только при отказе PostgreSQL или Redis. |

Полезные запросы LogQL:

```logql
{namespace="gosdoc", level="ERROR"}
{namespace="gosdoc", component=~"worker.*"} |= "Сбой"
sum by (component) (count_over_time({namespace="gosdoc", level="WARNING"}[1h]))
```

Пакет `evidently` ставится в окружение Airflow; без него шаг отчёта
пропускается с предупреждением в логе задачи. API Evidently различается между
версиями — шаг проверяется на контрольном стенде в контуре.
