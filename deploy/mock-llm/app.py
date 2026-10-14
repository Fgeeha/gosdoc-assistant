"""Заглушка OpenAI-совместимого API для разработки и тестов: детерминированные ответы, без сети.

Запуск: uvicorn app:app --app-dir deploy/mock-llm --port 8090
"""

import hashlib
import struct

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="mock-llm")
DIM = 64


class ChatIn(BaseModel):
    model: str
    messages: list[dict[str, str]]


class EmbIn(BaseModel):
    model: str
    input: str | list[str]


@app.get("/v1/models")
def models() -> dict[str, object]:
    return {
        "object": "list",
        "data": [{"id": "mock", "object": "model"}, {"id": "mock-embeddings", "object": "model"}],
    }


@app.post("/v1/chat/completions")
def chat(body: ChatIn) -> dict[str, object]:
    last = body.messages[-1]["content"] if body.messages else ""
    # Если просят JSON — отдаём пустой валидный объект, иначе краткое эхо.
    content = "{}" if "json" in last.lower() else f"[mock] получено {len(last)} символов"
    return {
        "id": "mock-" + hashlib.sha256(last.encode()).hexdigest()[:12],
        "object": "chat.completion",
        "model": body.model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


def _vector(text: str) -> list[float]:
    """Детерминированный единичный вектор из sha256 текста."""
    raw = b"".join(hashlib.sha256(f"{i}:{text}".encode()).digest() for i in range(DIM // 8))
    vec = [x / 2**32 - 0.5 for x in struct.unpack(f"{DIM}I", raw)]
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec]


@app.post("/v1/embeddings")
def embeddings(body: EmbIn) -> dict[str, object]:
    texts = [body.input] if isinstance(body.input, str) else body.input
    return {
        "object": "list",
        "model": body.model,
        "data": [
            {"object": "embedding", "index": i, "embedding": _vector(t)}
            for i, t in enumerate(texts)
        ],
    }
