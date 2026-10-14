"""JSON-логи в stdout (→ Loki) с маскированием персональных данных (152-ФЗ)."""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

# Порядок важен: сначала длинные и специфичные шаблоны, последним — «голые» 10-12 цифр (ИНН).
_PDN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"), "<card>"),
    (re.compile(r"\b\d{3}-\d{3}-\d{3}[ -]\d{2}\b"), "<snils>"),
    (re.compile(r"(?:\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}\b"), "<phone>"),
    # Серия и номер паспорта разделены пробелом или «№»; 10 цифр подряд считаем ИНН.
    (re.compile(r"\b\d{2}\s?\d{2}(?:\s|\s?№\s?)\d{6}\b"), "<passport>"),
    (re.compile(r"\b\d{10}(?:\d{1,2})?\b"), "<inn>"),
]


def mask_pdn(text: str) -> str:
    """Заменяет e-mail, номер карты, СНИЛС, телефон, паспорт, ИНН на метки."""
    for pattern, label in _PDN_PATTERNS:
        text = pattern.sub(label, text)
    return text


def mask_obj(value: Any) -> Any:
    """Рекурсивно маскирует строки в dict/list — для аудита и метрик."""
    if isinstance(value, str):
        return mask_pdn(value)
    if isinstance(value, dict):
        return {k: mask_obj(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_obj(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": mask_pdn(record.getMessage()),
        }
        if record.exc_info:
            entry["exc"] = mask_pdn(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """Все логгеры (включая uvicorn и dramatiq) пишут JSON через маскирующий форматтер."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "dramatiq"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    for noisy in ("pika", "httpx", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
