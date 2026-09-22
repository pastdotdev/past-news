from pathlib import Path

import httpx
import pytest

from newsroom.config import Config, Topic
from newsroom.web import create_app

from .fake_past import FakePast

FEED = (Path(__file__).parent / "fixtures" / "feed.xml").read_bytes()
TOPIC = Topic(slug="ai", name="AI regulation", api_key="past_sk_k", feeds=("https://example.tech/feed",))
CONFIG = Config(base_url="https://past.example", identity="newsroom", fetch_every_minutes=15, topics=(TOPIC,))


def routed(fake: FakePast) -> httpx.AsyncClient:
    """One transport for both the feeds and past, keyed by host."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.tech":
            return httpx.Response(200, content=FEED, headers={"Content-Type": "application/rss+xml"})
        return fake.handle(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


@pytest.fixture
async def browser():  # type: ignore[no-untyped-def]
    fake = FakePast()
    app = create_app(CONFIG, http=routed(fake), fetch_loop=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        yield client, fake, app


async def test_index_lists_topics(browser) -> None:  # type: ignore[no-untyped-def]
    client, _, _ = browser
    response = await client.get("/")
    assert response.status_code == 200
    assert "AI regulation" in response.text
    assert "not fetched yet" in response.text


async def test_topic_page_renders_briefing_sources_and_timeline(browser) -> None:  # type: ignore[no-untyped-def]
    client, fake, _ = browser
    response = await client.get("/t/ai?as_of=2026-09-08")

    assert response.status_code == 200
    body = response.text
    assert "Briefing" in body
    assert 'href="#story-1"' in body
    assert 'id="story-1" class="story cited"' in body
    assert "EU lawmakers agree on AI Act timeline" in body
    assert 'value="2026-09-08"' in body
    assert {c.path for c in fake.calls} == {"/api/v1/answer", "/api/v1/recall"}
    assert all(c.body["queryTimestamp"].startswith("2026-09-08T23:59:59") for c in fake.calls)


async def test_topic_page_answers_a_question(browser) -> None:  # type: ignore[no-untyped-def]
    client, fake, _ = browser
    response = await client.get("/t/ai", params={"q": "when does enforcement start?"})

    assert response.status_code == 200
    assert "when does enforcement start?" in response.text
    answer_call = next(c for c in fake.calls if c.path == "/api/v1/answer")
    assert answer_call.body["query"] == "when does enforcement start?"


async def test_topic_page_without_answerer_shows_evidence(browser) -> None:  # type: ignore[no-untyped-def]
    client, fake, _ = browser
    fake.answerer = False
    response = await client.get("/t/ai")
    assert response.status_code == 200
    assert "no answer model configured" in response.text


async def test_unknown_topic_and_bad_date(browser) -> None:  # type: ignore[no-untyped-def]
    client, _, _ = browser
    assert (await client.get("/t/nope")).status_code == 404
    assert (await client.get("/t/ai?as_of=yesterday")).status_code == 400


async def test_fetch_now_pushes_the_kept_articles(browser) -> None:  # type: ignore[no-untyped-def]
    client, fake, app = browser
    response = await client.post("/fetch", data={"slug": "ai"})
    assert response.status_code == 303
    assert response.headers["location"] == "/t/ai"

    # The fetch runs as a background task; drain it.
    import asyncio

    for _ in range(50):
        run = app.state.services.state.last("ai")
        if run and run.finished_at:
            break
        await asyncio.sleep(0.01)
    assert run is not None and run.finished_at is not None
    assert run.fetched == 3 and run.kept == 3 and run.changed == 3
    push = next(c for c in fake.calls if c.path == "/api/v1/ingest/batch")
    assert push.authorization == "Bearer past_sk_k"
    assert [item["id"] for item in push.body["items"]] == [
        "https://example.tech/undated",
        "https://example.tech/kettle",
        "https://example.tech/ai-act-timeline",
    ]
