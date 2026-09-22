"""An in-memory stand-in for the past API, mounted as an httpx transport.

It records every request and answers from canned pages. Tests read `calls` to assert what the
newsroom sent, and set `answerer=False` to behave like a deployment without an answer model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

RECALL_PAGE: dict[str, Any] = {
    "asOf": "2026-09-08T23:59:59Z",
    "usedEvidenceTokens": 300,
    "results": [
        {
            "artifactId": "a1",
            "kind": "source",
            "occurredAt": "2026-09-07T08:30:00Z",
            "documents": [
                {
                    "id": "doc-1",
                    "rank": 1,
                    "confidence": 0.9,
                    "occurredAt": "2026-09-07T08:30:00Z",
                    "content": "EU lawmakers agree on AI Act timeline\n\nEnforcement starts in 2027.\n\n"
                    "Source: Example Tech · https://example.tech/ai-act-timeline",
                    "sourceId": "https://example.tech/ai-act-timeline",
                    "excerpts": [],
                }
            ],
        },
        {
            "artifactId": "a2",
            "kind": "state",
            "occurredAt": "2026-09-08T10:00:00Z",
            "documents": [
                {
                    "id": "doc-2",
                    "rank": 2,
                    "confidence": 0.6,
                    "occurredAt": "2026-09-08T10:00:00Z",
                    "content": "The AI Act enforcement date is set to 2027.",
                    "attributes": {"status": "current"},
                    "excerpts": [
                        {
                            "sourceId": "https://example.tech/ai-act-timeline",
                            "occurredAt": "2026-09-07T08:30:00Z",
                            "content": "Enforcement starts in 2027.",
                        }
                    ],
                }
            ],
        },
    ],
}

ANSWER: dict[str, Any] = {
    **RECALL_PAGE,
    "answer": "Enforcement of the AI Act starts in 2027 d1.\n\nThat date is now settled d2.",
    "disposition": "answered",
    "citations": [{"documentId": "doc-1", "sourceIds": ["https://example.tech/ai-act-timeline"]}],
    "answerer": {"model": "fake/answerer"},
    "usage": {"totalTokens": 1, "costUsd": 0},
    "timings": {"recallMs": 1, "answerMs": 1, "totalMs": 2},
}


@dataclass
class Call:
    method: str
    path: str
    authorization: str | None
    body: Any


@dataclass
class FakePast:
    answerer: bool = True
    calls: list[Call] = field(default_factory=list)
    pushes: int = 0
    # A push reads as settled from this many status reads onwards (0: settled at once).
    settle_after: int = 0
    status_reads: int = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport())

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append(Call(request.method, request.url.path, request.headers.get("authorization"), body))
        if request.url.path == "/api/v1/ingest/batch":
            self.pushes += 1
            items = body["items"]
            receipt = [
                {
                    "ordinal": i,
                    "sourceId": item["id"],
                    "status": "accepted",
                    "unchanged": item["id"].endswith("/old"),
                }
                for i, item in enumerate(items)
            ]
            return httpx.Response(
                200,
                json={
                    "projectId": 1,
                    "ingestionId": f"push-{self.pushes}",
                    "status": "accepted",
                    "items": receipt,
                },
            )
        if request.url.path.startswith("/api/v1/ingest/"):
            self.status_reads += 1
            settled = self.status_reads > self.settle_after
            return httpx.Response(
                200,
                json={"status": "settled" if settled else "processing", "settled": settled, "blocked": False},
            )
        if request.url.path == "/api/v1/recall":
            return httpx.Response(200, json=RECALL_PAGE)
        if request.url.path == "/api/v1/answer":
            if not self.answerer:
                return httpx.Response(
                    503, json={"code": "answerer-not-configured", "status": 503, "message": "no answerer"}
                )
            return httpx.Response(200, json=ANSWER)
        return httpx.Response(404, json={"code": "not-found", "status": 404})
