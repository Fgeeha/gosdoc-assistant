"""Клиент OpenAI-совместимого API (Inference / Sherpa / mock), ADR-0008."""

from typing import Any

import httpx

from gosdoc.core.config import Settings


class LLMClient:
    def __init__(self, s: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.s = s
        verify: Any = s.ca_bundle or True
        self.chat_http = httpx.Client(
            base_url=s.llm_base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {s.llm_api_key}"},
            timeout=s.llm_timeout_s,
            verify=verify,
            transport=transport,
        )
        self.emb_http = httpx.Client(
            base_url=(s.embeddings_base_url or s.llm_base_url).rstrip("/") + "/",
            headers={"Authorization": f"Bearer {s.embeddings_api_key or s.llm_api_key}"},
            timeout=s.llm_timeout_s,
            verify=verify,
            transport=transport,
        )

    def chat(self, messages: list[dict[str, str]], **params: Any) -> str:
        """Возвращает текст первого варианта ответа."""
        body = {"model": self.s.llm_model, "messages": messages, "temperature": 0, **params}
        r = self.chat_http.post("chat/completions", json=body)
        r.raise_for_status()
        return str(r.json()["choices"][0]["message"]["content"])

    def embed(self, texts: list[str]) -> list[list[float]]:
        r = self.emb_http.post(
            "embeddings", json={"model": self.s.embeddings_model, "input": texts}
        )
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]

    def close(self) -> None:
        self.chat_http.close()
        self.emb_http.close()
