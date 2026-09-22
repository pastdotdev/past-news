from datetime import UTC, datetime

import pytest

from newsroom.feeds import Article
from newsroom.ingest import PUSH_SIZE, NotSettled, push_articles, to_data_point, wait_settled
from newsroom.past import PastClient

from .fake_past import FakePast

NOW = datetime(2026, 9, 7, 8, 30, tzinfo=UTC)


def article(n: int, url: str | None = None) -> Article:
    return Article(
        url=url or f"https://example.tech/{n}",
        title=f"Story {n}",
        summary="Body." if n % 2 else "",
        published=NOW,
        outlet="Example Tech",
        feed_url="https://example.tech/feed",
    )


def test_data_point_layout_is_headline_body_source() -> None:
    point = to_data_point(article(1))

    assert point.id == "https://example.tech/1"
    assert point.content == "Story 1\n\nBody.\n\nSource: Example Tech · https://example.tech/1"
    assert point.timestamp == "2026-09-07T08:30:00+00:00"
    assert point.label == "Story 1"
    assert point.metadata == {
        "url": "https://example.tech/1",
        "outlet": "Example Tech",
        "feed": "https://example.tech/feed",
    }
    assert to_data_point(article(2)).content == "Story 2\n\nSource: Example Tech · https://example.tech/2"


async def test_push_dedupes_by_url_and_batches() -> None:
    fake = FakePast()
    articles = [article(n) for n in range(PUSH_SIZE + 5)] + [
        article(0),
        article(9, "https://example.tech/old"),
    ]
    async with fake.client() as http:
        summary = await push_articles(PastClient("past_sk_k", "https://past.example", http), articles)

    assert summary.pushed == PUSH_SIZE + 6
    assert summary.changed == PUSH_SIZE + 5
    assert summary.unchanged == 1
    assert summary.ingestion_ids == ("push-1", "push-2")
    assert [len(call.body["items"]) for call in fake.calls] == [PUSH_SIZE, 6]
    assert fake.calls[0].authorization == "Bearer past_sk_k"
    assert fake.calls[0].path == "/api/v1/ingest/batch"


async def test_push_refuses_an_oversized_batch() -> None:
    async with FakePast().client() as http:
        client = PastClient("past_sk_k", "https://past.example", http)
        with pytest.raises(ValueError):
            await client.push([to_data_point(article(n)) for n in range(1001)])


async def test_wait_polls_until_every_push_is_settled() -> None:
    fake = FakePast(settle_after=2)
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    async with fake.client() as http:
        client = PastClient("past_sk_k", "https://past.example", http)
        await wait_settled(client, ("push-1",), interval=0.5, sleep=sleep)

    assert fake.status_reads == 3
    assert slept == [0.5, 0.5]
    assert fake.calls[-1].path == "/api/v1/ingest/push-1"


async def test_wait_gives_up_after_the_timeout() -> None:
    fake = FakePast(settle_after=100)

    async def sleep(seconds: float) -> None:
        pass

    async with fake.client() as http:
        client = PastClient("past_sk_k", "https://past.example", http)
        with pytest.raises(NotSettled, match="1 push\\(es\\) still processing after 1s"):
            await wait_settled(client, ("push-1",), interval=0.5, timeout=1, sleep=sleep)
