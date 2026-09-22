from datetime import date

import pytest

from newsroom.config import Topic
from newsroom.past import PastApiError, PastClient
from newsroom.reader import ask, briefing, render_citations, split_content, timeline

from .fake_past import FakePast

TOPIC = Topic(slug="ai", name="AI regulation", api_key="past_sk_k", feeds=("https://example.tech/feed",))


def client(fake: FakePast) -> PastClient:
    return PastClient("past_sk_k", "https://past.example", fake.client())


async def test_briefing_uses_answer_with_as_of_and_marks_cited() -> None:
    fake = FakePast()
    article = await briefing(client(fake), TOPIC, "newsroom", date(2026, 9, 8))

    assert article.text is not None and article.text.startswith("Enforcement")
    assert article.model == "fake/answerer"
    assert [(s.number, s.cited) for s in article.stories] == [(1, True), (2, False)]
    call = fake.calls[0]
    assert call.path == "/api/v1/answer"
    assert call.body["identity"] == "newsroom"
    assert call.body["queryTimestamp"] == "2026-09-08T23:59:59.999999+00:00"
    assert "AI regulation" in call.body["query"]
    assert "briefing" in call.body["instructions"]


async def test_ask_falls_back_to_evidence_without_an_answerer() -> None:
    fake = FakePast(answerer=False)
    article = await ask(client(fake), TOPIC, "newsroom", "when does enforcement start?", None)

    assert article.evidence_only
    assert [s.number for s in article.stories] == [1, 2]
    assert [c.path for c in fake.calls] == ["/api/v1/answer", "/api/v1/recall"]
    assert "queryTimestamp" not in fake.calls[1].body


async def test_other_refusals_propagate() -> None:
    fake = FakePast()

    def refuse(request):  # type: ignore[no-untyped-def]
        import httpx

        return httpx.Response(401, json={"code": "unauthorized", "status": 401, "message": "bad key"})

    fake.handle = refuse  # type: ignore[method-assign]
    with pytest.raises(PastApiError) as raised:
        await ask(client(fake), TOPIC, "newsroom", "q", None)
    assert raised.value.status == 401
    assert raised.value.code == "unauthorized"


async def test_timeline_is_newest_first_with_outlet_and_link() -> None:
    fake = FakePast()
    stories = await timeline(client(fake), TOPIC, "newsroom", None)

    assert [s.occurred_at[:10] for s in stories] == ["2026-09-08", "2026-09-07"]
    assert stories[1].headline == "EU lawmakers agree on AI Act timeline"
    assert stories[1].outlet == "Example Tech"
    assert stories[1].url == "https://example.tech/ai-act-timeline"
    assert stories[0].headline == ""  # a consolidated memory has no headline line
    assert stories[0].body == "The AI Act enforcement date is set to 2027."
    assert fake.calls[0].body["sort"] == "chronological"


def test_split_content_reads_the_ingest_layout_back() -> None:
    assert split_content("Head\n\nBody one\nBody two\n\nSource: Outlet · https://x/y") == (
        "Head",
        "Body one\nBody two",
        "Outlet",
        "https://x/y",
    )
    assert split_content("Just a derived memory.") == ("", "Just a derived memory.", None, None)
    assert split_content("") == ("", "", None, None)


def test_render_citations_links_known_numbers_and_escapes() -> None:
    html = render_citations("<b>Bold</b> claim d1 and d9.", frozenset({1}))
    assert html == '&lt;b&gt;Bold&lt;/b&gt; claim <sup><a class="cite" href="#story-1">1</a></sup> and d9.'
