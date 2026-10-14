"""Гибридный поиск по НПА: полнотекстовый (Elasticsearch) + векторный (Chroma / Milvus).

Результаты объединяются Reciprocal Rank Fusion. Хранилища — по REST через
httpx, без клиентских библиотек: адреса и CA — из конфигурации.
"""

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

RRF_K = 60


@dataclass(frozen=True)
class IndexedChunk:
    id: str  # npa_chunks.id строкой
    text: str
    act_id: int
    edition_id: int
    organization_id: int | None  # None — общий акт
    path: str


class TextIndex(Protocol):
    def ensure(self) -> None: ...
    def add(self, chunks: Sequence[IndexedChunk]) -> None: ...
    def delete_edition(self, edition_id: int) -> None: ...
    def search(self, query: str, organization_id: int, k: int) -> list[str]: ...


class VectorIndex(Protocol):
    def ensure(self, dim: int) -> None: ...
    def add(self, chunks: Sequence[IndexedChunk], vectors: Sequence[Sequence[float]]) -> None: ...
    def delete_edition(self, edition_id: int) -> None: ...
    def search(self, vector: Sequence[float], organization_id: int, k: int) -> list[str]: ...


def rrf(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[str]:
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] += 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda d: (-scores[d], d))


def _visible_to(organization_id: int) -> list[int]:
    """Общие акты хранятся с organization_id = 0 в индексах (там нет NULL)."""
    return [0, organization_id]


class ElasticIndex:
    """Индекс Elasticsearch с русским анализатором (стемминг)."""

    def __init__(
        self, url: str, index: str = "npa_chunks", verify: Any = True, timeout: float = 10
    ) -> None:
        self.http = httpx.Client(base_url=url.rstrip("/"), verify=verify, timeout=timeout)
        self.index = index

    def ensure(self) -> None:
        if self.http.head(f"/{self.index}").status_code != 200:
            self._create()
        # Сразу после создания шарды ещё не размещены и запросы получают 503.
        r = self.http.get(
            f"/_cluster/health/{self.index}", params={"wait_for_status": "yellow", "timeout": "30s"}
        )
        r.raise_for_status()

    def _create(self) -> None:
        mapping = {
            "mappings": {
                "properties": {
                    "text": {"type": "text", "analyzer": "russian"},
                    "path": {"type": "keyword"},
                    "act_id": {"type": "integer"},
                    "edition_id": {"type": "integer"},
                    "organization_id": {"type": "integer"},
                }
            }
        }
        r = self.http.put(f"/{self.index}", json=mapping)
        if r.status_code != 400 or "resource_already_exists" not in r.text:
            r.raise_for_status()

    def add(self, chunks: Sequence[IndexedChunk]) -> None:
        if not chunks:
            return
        lines: list[dict[str, Any]] = []
        for c in chunks:
            lines.append({"index": {"_index": self.index, "_id": c.id}})
            lines.append(
                {
                    "text": c.text,
                    "path": c.path,
                    "act_id": c.act_id,
                    "edition_id": c.edition_id,
                    "organization_id": c.organization_id or 0,
                }
            )
        body = "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n"
        r = self.http.post(
            "/_bulk?refresh=true",
            content=body.encode(),
            headers={"Content-Type": "application/x-ndjson"},
        )
        r.raise_for_status()
        if r.json().get("errors"):
            raise RuntimeError("Elasticsearch: ошибки при индексации фрагментов")

    def delete_edition(self, edition_id: int) -> None:
        r = self.http.post(
            f"/{self.index}/_delete_by_query?refresh=true",
            json={"query": {"term": {"edition_id": edition_id}}},
        )
        if r.status_code != 404:
            r.raise_for_status()

    def search(self, query: str, organization_id: int, k: int) -> list[str]:
        body = {
            "size": k,
            "_source": False,
            "query": {
                "bool": {
                    "must": {"match": {"text": query}},
                    "filter": {"terms": {"organization_id": _visible_to(organization_id)}},
                }
            },
        }
        r = self.http.post(f"/{self.index}/_search", json=body)
        r.raise_for_status()
        return [h["_id"] for h in r.json()["hits"]["hits"]]


class ChromaIndex:
    """ChromaDB (локальная разработка), REST API v2."""

    def __init__(
        self, url: str, collection: str = "npa_chunks", verify: Any = True, timeout: float = 10
    ) -> None:
        base = url.rstrip("/") + "/api/v2/tenants/default_tenant/databases/default_database"
        self.http = httpx.Client(base_url=base, verify=verify, timeout=timeout)
        self.collection = collection
        self._id: str | None = None

    def ensure(self, dim: int) -> None:
        r = self.http.post(
            "/collections",
            json={
                "name": self.collection,
                "get_or_create": True,
                "configuration": {"hnsw": {"space": "cosine"}},
            },
        )
        r.raise_for_status()
        self._id = r.json()["id"]

    def _cid(self) -> str:
        if self._id is None:
            self.ensure(0)
        return self._id or ""

    def add(self, chunks: Sequence[IndexedChunk], vectors: Sequence[Sequence[float]]) -> None:
        if not chunks:
            return
        r = self.http.post(
            f"/collections/{self._cid()}/upsert",
            json={
                "ids": [c.id for c in chunks],
                "embeddings": [list(v) for v in vectors],
                "metadatas": [
                    {
                        "edition_id": c.edition_id,
                        "act_id": c.act_id,
                        "organization_id": c.organization_id or 0,
                    }
                    for c in chunks
                ],
            },
        )
        r.raise_for_status()

    def delete_edition(self, edition_id: int) -> None:
        r = self.http.post(
            f"/collections/{self._cid()}/delete", json={"where": {"edition_id": edition_id}}
        )
        r.raise_for_status()

    def search(self, vector: Sequence[float], organization_id: int, k: int) -> list[str]:
        r = self.http.post(
            f"/collections/{self._cid()}/query",
            json={
                "query_embeddings": [list(vector)],
                "n_results": k,
                "where": {"organization_id": {"$in": _visible_to(organization_id)}},
                "include": ["distances"],
            },
        )
        r.raise_for_status()
        return list(r.json()["ids"][0])


class MilvusIndex:
    """MilvusCluster (прод), REST API v2. Проверяется на контрольном стенде в контуре."""

    def __init__(
        self,
        url: str,
        collection: str = "npa_chunks",
        token: str | None = None,
        verify: Any = True,
        timeout: float = 10,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.Client(
            base_url=url.rstrip("/") + "/v2/vectordb",
            headers=headers,
            verify=verify,
            timeout=timeout,
        )
        self.collection = collection

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        r = self.http.post(path, json={"collectionName": self.collection, **body})
        r.raise_for_status()
        data = r.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(f"Milvus: {data.get('message')}")
        return data.get("data")

    def ensure(self, dim: int) -> None:
        if self._post("/collections/has", {}).get("has"):
            return
        self._post(
            "/collections/create",
            {
                "dimension": dim,
                "metricType": "COSINE",
                "idType": "VarChar",
                "params": {"max_length": 64},
            },
        )

    def add(self, chunks: Sequence[IndexedChunk], vectors: Sequence[Sequence[float]]) -> None:
        if chunks:
            self._post(
                "/entities/upsert",
                {
                    "data": [
                        {
                            "id": c.id,
                            "vector": list(v),
                            "edition_id": c.edition_id,
                            "organization_id": c.organization_id or 0,
                        }
                        for c, v in zip(chunks, vectors, strict=True)
                    ]
                },
            )

    def delete_edition(self, edition_id: int) -> None:
        self._post("/entities/delete", {"filter": f"edition_id == {int(edition_id)}"})

    def search(self, vector: Sequence[float], organization_id: int, k: int) -> list[str]:
        orgs = ", ".join(str(o) for o in _visible_to(int(organization_id)))
        hits = self._post(
            "/entities/search",
            {
                "data": [list(vector)],
                "annsField": "vector",
                "limit": k,
                "filter": f"organization_id in [{orgs}]",
                "outputFields": ["id"],
            },
        )
        return [str(h["id"]) for h in hits]
