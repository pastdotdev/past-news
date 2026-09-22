"""The fetch loop: every feed of every topic, pushed to that topic's project.

One run is a plain function so `newsroom fetch` and the in-process loop of `newsroom serve`
share it. State is in memory and only describes the last run; past is the store.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from .config import Config, Topic
from .feeds import Article, fetch_feed, matches_keywords
from .ingest import push_articles
from .past import PastClient

log = logging.getLogger(__name__)


@dataclass
class TopicRun:
    started_at: datetime
    finished_at: datetime | None = None
    fetched: int = 0
    kept: int = 0
    changed: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    ingestion_ids: tuple[str, ...] = ()


@dataclass
class FetchState:
    runs: dict[str, TopicRun] = field(default_factory=dict)
    running: bool = False

    def last(self, slug: str) -> TopicRun | None:
        return self.runs.get(slug)


async def run_topic(topic: Topic, config: Config, http: httpx.AsyncClient) -> TopicRun:
    run = TopicRun(started_at=datetime.now(UTC))
    articles: list[Article] = []
    for feed_url in topic.feeds:
        try:
            fetched = await fetch_feed(http, feed_url)
        except (httpx.HTTPError, ValueError) as error:
            run.errors.append(f"{feed_url}: {error}")
            log.warning("topic %s: feed %s failed: %s", topic.slug, feed_url, error)
            continue
        run.fetched += len(fetched)
        articles.extend(article for article in fetched if matches_keywords(article, topic.keywords))
    run.kept = len(articles)
    if articles:
        client = PastClient(topic.api_key, config.base_url, http)
        try:
            summary = await push_articles(client, articles)
            run.changed, run.unchanged = summary.changed, summary.unchanged
            run.ingestion_ids = summary.ingestion_ids
        except Exception as error:  # noqa: BLE001 - one topic's failure must not stop the others
            run.errors.append(f"push: {error}")
            log.warning("topic %s: push failed: %s", topic.slug, error)
    run.finished_at = datetime.now(UTC)
    log.info(
        "topic %s: %d fetched, %d kept, %d new, %d unchanged, %d errors",
        topic.slug,
        run.fetched,
        run.kept,
        run.changed,
        run.unchanged,
        len(run.errors),
    )
    return run


async def run_once(
    config: Config, http: httpx.AsyncClient, state: FetchState, only: str | None = None
) -> None:
    if state.running:
        return
    state.running = True
    try:
        for topic in config.topics:
            if only is not None and topic.slug != only:
                continue
            state.runs[topic.slug] = await run_topic(topic, config, http)
    finally:
        state.running = False


async def run_forever(config: Config, http: httpx.AsyncClient, state: FetchState) -> None:
    """Fetches now, then every `fetch_every_minutes`. Cancelled with the web server."""
    while True:
        try:
            await run_once(config, http, state)
        except Exception:  # noqa: BLE001 - the loop outlives any single failure
            log.exception("fetch run failed")
        await asyncio.sleep(config.fetch_every_minutes * 60)
