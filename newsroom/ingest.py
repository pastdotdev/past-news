"""Turning articles into data points and pushing them.

The content layout is a contract with the reader: the first line is the headline, the last line
names the outlet and the link. Recall returns the content exactly as stored, so the timeline can
read those two lines back.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .feeds import Article
from .past import DataPoint, PastClient

PUSH_SIZE = 200
SOURCE_PREFIX = "Source: "


def to_data_point(article: Article) -> DataPoint:
    body = f"{article.title}\n\n{article.summary}\n\n" if article.summary else f"{article.title}\n\n"
    return DataPoint(
        id=article.url,
        content=f"{body}{SOURCE_PREFIX}{article.outlet} · {article.url}",
        timestamp=article.published.isoformat(),
        label=article.title[:200],
        metadata={"url": article.url, "outlet": article.outlet, "feed": article.feed_url},
    )


@dataclass(frozen=True)
class PushSummary:
    pushed: int
    changed: int
    unchanged: int
    ingestion_ids: tuple[str, ...]


async def push_articles(client: PastClient, articles: list[Article]) -> PushSummary:
    """Pushes every article, deduplicated by URL, in pushes of PUSH_SIZE. past reports which
    points actually changed; re-pushing yesterday's articles costs one request and no processing."""
    unique: dict[str, Article] = {}
    for article in articles:
        unique.setdefault(article.url, article)
    points = [to_data_point(article) for article in unique.values()]
    changed = unchanged = 0
    ids: list[str] = []
    for start in range(0, len(points), PUSH_SIZE):
        receipt = await client.push(points[start : start + PUSH_SIZE])
        changed += receipt.changed
        unchanged += receipt.unchanged
        ids.append(receipt.ingestion_id)
    return PushSummary(pushed=len(points), changed=changed, unchanged=unchanged, ingestion_ids=tuple(ids))


class NotSettled(Exception):
    """Raised when pushes are still processing after the timeout, or past reports one blocked."""


async def wait_settled(
    client: PastClient,
    ingestion_ids: tuple[str, ...],
    *,
    interval: float = 3.0,
    timeout: float = 15 * 60,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Returns once every push is settled, which is when its articles are readable through
    recall and answer."""
    pending = set(ingestion_ids)
    waited = 0.0
    while pending:
        for ingestion_id in sorted(pending):
            status = await client.status(ingestion_id)
            if status.blocked:
                raise NotSettled(f"push {ingestion_id} is blocked ({status.status})")
            if status.settled:
                pending.discard(ingestion_id)
        if not pending:
            return
        if waited >= timeout:
            raise NotSettled(f"{len(pending)} push(es) still processing after {timeout:.0f}s")
        await sleep(interval)
        waited += interval
