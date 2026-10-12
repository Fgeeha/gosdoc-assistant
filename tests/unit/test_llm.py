import json

import httpx
from gosdoc.core.config import Settings
from gosdoc.core.llm import LLMClient


def test_chat_and_embeddings_use_configured_urls() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/chat/completions"):
            assert json.loads(request.content)["model"] == "m1"
            return httpx.Response(200, json={"choices": [{"message": {"content": "ответ"}}]})
        return httpx.Response(
            200, json={"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}
        )

    s = Settings(
        llm_base_url="http://llm.fgau.local/v1",
        llm_model="m1",
        embeddings_base_url="http://emb.fgau.local/v1",
    )
    client = LLMClient(s, transport=httpx.MockTransport(handler))

    assert client.chat([{"role": "user", "content": "привет"}]) == "ответ"
    assert client.embed(["a", "b"]) == [[1.0], [2.0]]
    assert seen == [
        "http://llm.fgau.local/v1/chat/completions",
        "http://emb.fgau.local/v1/embeddings",
    ]
