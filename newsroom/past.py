"""A small typed client for the past.dev Memory API endpoints the newsroom uses.

Reference: https://past.dev/docs/memory-api/api-reference
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx


class PastApiError(Exception):
    """Any refusal from the API. `code` is the stable error code from the body when there is one."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class DataPoint:
    """One item of a push. `id` is the caller's stable identifier; re-pushing the same id with
    the same content is a no-op on past's side."""

    id: str
    content: str
    timestamp: str
    label: str | None = None
    metadata: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {"id": self.id, "content": self.content, "timestamp": self.timestamp}
        if self.label is not None:
            body["label"] = self.label
        if self.metadata is not None:
            body["metadata"] = self.metadata
        return body


@dataclass(frozen=True)
class PushReceipt:
    ingestion_id: str
    status: str
    changed: int
    unchanged: int


@dataclass(frozen=True)
class IngestionStatus:
    status: str
    settled: bool
    blocked: bool


@dataclass(frozen=True)
class Excerpt:
    source_id: str
    occurred_at: str
    content: str


@dataclass(frozen=True)
class Document:
    id: str
    rank: int
    confidence: float | None
    occurred_at: str
    content: str
    kind: str
    source_id: str | None
    attributes: dict[str, str]
    excerpts: tuple[Excerpt, ...]


@dataclass(frozen=True)
class RecallPage:
    as_of: str
    documents: tuple[Document, ...]


@dataclass(frozen=True)
class Answer:
    text: str
    disposition: Literal["answered", "abstained", "clarification_required"]
    cited_document_ids: frozenset[str]
    model: str
    page: RecallPage


class PastClient:
    """Async client for one project key."""

    def __init__(self, api_key: str, base_url: str, http: httpx.AsyncClient) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._http = http

    async def push(self, points: list[DataPoint], idempotency_key: str | None = None) -> PushReceipt:
        """POST /api/v1/ingest/batch: one ordered push of up to 1,000 points."""
        if not 0 < len(points) <= 1000:
            raise ValueError("a push carries between 1 and 1,000 points")
        body: dict[str, Any] = {"items": [point.to_json() for point in points]}
        if idempotency_key:
            body["idempotencyKey"] = idempotency_key
        data = await self._post("/api/v1/ingest/batch", body)
        items = data.get("items", [])
        unchanged = sum(1 for item in items if item.get("unchanged"))
        return PushReceipt(
            ingestion_id=str(data["ingestionId"]),
            status=str(data["status"]),
            changed=len(items) - unchanged,
            unchanged=unchanged,
        )

    async def status(self, ingestion_id: str) -> IngestionStatus:
        """GET /api/v1/ingest/{id}: where one push stands. `settled` means readable."""
        response = await self._http.get(
            f"{self._base_url}/api/v1/ingest/{ingestion_id}", headers=self._headers()
        )
        data = _read(response)
        return IngestionStatus(
            status=str(data.get("status", "")),
            settled=bool(data.get("settled")),
            blocked=bool(data.get("blocked")),
        )

    async def recall(
        self,
        query: str,
        identity: str,
        *,
        as_of: str | None = None,
        limit: int = 20,
        max_tokens: int = 8000,
        sort: Literal["relevance", "chronological"] = "relevance",
    ) -> RecallPage:
        body = _read_request(query, identity, as_of, limit, max_tokens, sort)
        return _page(await self._post("/api/v1/recall", body))

    async def answer(
        self,
        query: str,
        identity: str,
        *,
        instructions: str | None = None,
        as_of: str | None = None,
        limit: int = 20,
        max_tokens: int = 8000,
        sort: Literal["relevance", "chronological"] = "relevance",
    ) -> Answer:
        body = _read_request(query, identity, as_of, limit, max_tokens, sort)
        if instructions:
            body["instructions"] = instructions
        data = await self._post("/api/v1/answer", body)
        return Answer(
            text=str(data["answer"]),
            disposition=data["disposition"],
            cited_document_ids=frozenset(str(c["documentId"]) for c in data.get("citations", [])),
            model=str(data.get("answerer", {}).get("model", "")),
            page=_page(data),
        )

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._http.post(f"{self._base_url}{path}", json=body, headers=self._headers())
        return _read(response)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}


def _read_request(
    query: str, identity: str, as_of: str | None, limit: int, max_tokens: int, sort: str
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "query": query,
        "identity": identity,
        "limit": limit,
        "maxTokens": max_tokens,
        "sort": sort,
    }
    if as_of:
        body["queryTimestamp"] = as_of
    return body


def _read(response: httpx.Response) -> dict[str, Any]:
    if response.is_success:
        parsed = response.json()
        return parsed if isinstance(parsed, dict) else {}
    code, message = "unknown", f"past.dev API responded {response.status_code}"
    try:
        body = response.json()
        if isinstance(body, dict):
            code = str(body.get("code", code))
            message = str(body.get("message") or message)
    except ValueError:
        pass
    raise PastApiError(response.status_code, code, message)


def _page(data: dict[str, Any]) -> RecallPage:
    documents: list[Document] = []
    for result in data.get("results", []):
        kind = str(result.get("kind", "memory"))
        for document in result.get("documents", []):
            documents.append(
                Document(
                    id=str(document["id"]),
                    rank=int(document["rank"]),
                    confidence=document.get("confidence"),
                    occurred_at=str(document["occurredAt"]),
                    content=str(document["content"]),
                    kind=kind,
                    source_id=document.get("sourceId"),
                    attributes={str(k): str(v) for k, v in (document.get("attributes") or {}).items()},
                    excerpts=tuple(
                        Excerpt(str(e["sourceId"]), str(e["occurredAt"]), str(e["content"]))
                        for e in document.get("excerpts", [])
                    ),
                )
            )
    return RecallPage(as_of=str(data["asOf"]), documents=tuple(documents))
