"""Индексы в памяти и модель-заглушка для тестов модуля проверки документов."""

import json
import re
from collections.abc import Sequence

import httpx
from gosdoc.core.config import Settings
from gosdoc.core.llm import LLMClient
from gosdoc.docs_check.npa import SearchBackends

from rag.search import IndexedChunk


def _words(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[а-яё]{4,}", text.lower())}


class MemoryText:
    def __init__(self) -> None:
        self.chunks: dict[str, IndexedChunk] = {}

    def ensure(self) -> None: ...

    def add(self, chunks: Sequence[IndexedChunk]) -> None:
        self.chunks.update({c.id: c for c in chunks})

    def delete_edition(self, edition_id: int) -> None:
        self.chunks = {k: c for k, c in self.chunks.items() if c.edition_id != edition_id}

    def search(self, query: str, organization_id: int, k: int) -> list[str]:
        q = _words(query)
        scored = [
            (len(q & _words(c.text)), c.id)
            for c in self.chunks.values()
            if (c.organization_id or 0) in (0, organization_id)
        ]
        return [i for s, i in sorted(scored, reverse=True) if s][:k]


class MemoryVectors:
    def __init__(self, text: MemoryText) -> None:
        self.text = text

    def ensure(self, dim: int) -> None: ...
    def add(self, chunks: Sequence[IndexedChunk], vectors: Sequence[Sequence[float]]) -> None: ...
    def delete_edition(self, edition_id: int) -> None: ...

    def search(self, vector: Sequence[float], organization_id: int, k: int) -> list[str]:
        return []  # ранжирование в тестах — по тексту


def checking_model(prompts: list[str]) -> LLMClient:
    """Находит в нормах «ст. 12 ч. 1» и сообщает о нарушении срока с дословной цитатой;
    на абзац о согласии отвечает выдуманной цитатой (такой ответ должен быть отброшен)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/embeddings"):
            texts = json.loads(request.content)["input"]
            return httpx.Response(
                200,
                json={"data": [{"index": i, "embedding": [1.0, 0.0]} for i in range(len(texts))]},
            )
        user = json.loads(request.content)["messages"][1]["content"]
        prompts.append(user)
        norms = re.findall(
            r"^\[(\d+)\] .*?, (ст\. [\d.]+(?: ч\. [\d.]+)?(?: п\. \d+)?): (.*)$", user, re.M
        )
        answer: dict[str, object] = {"verdict": "ok"}
        if "60 дней" in user:
            for n, path, _text in norms:
                if path == "ст. 12 ч. 1":
                    answer = {
                        "verdict": "violation",
                        "source": int(n),
                        "quote": "рассматривается в течение 30 дней со дня его регистрации",
                        "explanation": "Срок рассмотрения указан 60 дней, закон устанавливает 30 дней",
                        "recommendation": "Замените «60 дней» на «30 дней»",
                    }
        elif "согласия" in user and norms:
            answer = {
                "verdict": "violation",
                "source": 1,
                "quote": "согласие должно быть получено в письменной форме нотариально",
                "explanation": "выдумано",
                "recommendation": "—",
            }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(answer, ensure_ascii=False)}}]},
        )

    return LLMClient(Settings(llm_model="test-model"), transport=httpx.MockTransport(handler))


def backends(prompts: list[str]) -> SearchBackends:
    text = MemoryText()
    return SearchBackends(text, MemoryVectors(text), checking_model(prompts))
