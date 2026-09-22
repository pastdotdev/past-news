"""The read side: a briefing, an answer to a question, and a timeline, all as of a chosen day.

Everything here is a recall or an answer over the topic's project. `as_of` is passed straight
through as past's `queryTimestamp`, which is what makes "what did we know on that day" work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from .config import Topic
from .ingest import SOURCE_PREFIX
from .past import Answer, Document, PastApiError, PastClient, RecallPage

CITATION = re.compile(r"\b[dD]([1-9]\d*)\b")

BRIEFING_INSTRUCTIONS = (
    "Write a news briefing of the most recent developments, newest first, as three to six short "
    "paragraphs. Each paragraph covers one development and ends with its dN citation. Name the outlet "
    "when it matters. No headings, no bullet points."
)
ANSWER_INSTRUCTIONS = (
    "Answer directly from the news held here, citing each claim with its dN reference. If the "
    "coverage does not say, say so."
)


@dataclass(frozen=True)
class Story:
    """A recalled document as the timeline shows it."""

    number: int
    document_id: str
    kind: str
    occurred_at: str
    headline: str
    body: str
    outlet: str | None
    url: str | None
    cited: bool


@dataclass(frozen=True)
class Article:
    """A written briefing or answer, or the evidence alone when the deployment has no answerer."""

    text: str | None
    disposition: str | None
    model: str | None
    as_of: str
    stories: tuple[Story, ...]

    @property
    def evidence_only(self) -> bool:
        return self.text is None


def as_of_instant(day: date | None) -> str | None:
    """The end of that day in UTC, so everything published on it is in scope."""
    return None if day is None else datetime.combine(day, time.max, tzinfo=UTC).isoformat()


async def briefing(client: PastClient, topic: Topic, identity: str, day: date | None) -> Article:
    query = f"What are the latest developments in {topic.name}?"
    return await _article(client, query, identity, BRIEFING_INSTRUCTIONS, day)


async def ask(client: PastClient, topic: Topic, identity: str, question: str, day: date | None) -> Article:
    return await _article(client, question, identity, ANSWER_INSTRUCTIONS, day)


async def timeline(client: PastClient, topic: Topic, identity: str, day: date | None) -> tuple[Story, ...]:
    """The most relevant recent stories, newest first. Recall selects by relevance to the topic,
    then presents chronologically; we flip that so the newest is on top."""
    page = await client.recall(
        f"news about {topic.name}",
        identity,
        as_of=as_of_instant(day),
        limit=40,
        max_tokens=12000,
        sort="chronological",
    )
    stories = stories_from(page, cited=frozenset())
    return tuple(sorted(stories, key=lambda story: story.occurred_at, reverse=True))


async def _article(
    client: PastClient, query: str, identity: str, instructions: str, day: date | None
) -> Article:
    as_of = as_of_instant(day)
    try:
        answer = await client.answer(query, identity, instructions=instructions, as_of=as_of)
    except PastApiError as error:
        if error.code != "answerer-not-configured":
            raise
        page = await client.recall(query, identity, as_of=as_of)
        return Article(
            text=None, disposition=None, model=None, as_of=page.as_of, stories=stories_from(page, frozenset())
        )
    return article_from(answer)


def article_from(answer: Answer) -> Article:
    return Article(
        text=answer.text,
        disposition=answer.disposition,
        model=answer.model,
        as_of=answer.page.as_of,
        stories=stories_from(answer.page, answer.cited_document_ids),
    )


def stories_from(page: RecallPage, cited: frozenset[str]) -> tuple[Story, ...]:
    return tuple(story_from(document, document.id in cited) for document in page.documents)


def story_from(document: Document, cited: bool) -> Story:
    headline, body, outlet, url = split_content(document.content)
    return Story(
        number=document.rank,
        document_id=document.id,
        kind=document.kind,
        occurred_at=document.occurred_at,
        headline=headline,
        body=body,
        outlet=outlet,
        url=url,
        cited=cited,
    )


def split_content(content: str) -> tuple[str, str, str | None, str | None]:
    """Reads back the layout ingest wrote: headline first, "Source: outlet · url" last. Consolidated
    memories past derived have neither, and come back as headline-less bodies."""
    lines = [line for line in content.strip().splitlines() if line.strip()]
    if not lines:
        return "", "", None, None
    outlet = url = None
    if lines[-1].startswith(SOURCE_PREFIX):
        source = lines.pop().removeprefix(SOURCE_PREFIX)
        outlet, _, url = source.partition(" · ")
        outlet = outlet.strip() or None
        url = url.strip() or None
    if outlet is None:
        return "", "\n".join(lines), None, None
    headline = lines[0].strip()
    body = "\n".join(lines[1:]).strip()
    return headline, body, outlet, url


def render_citations(text: str, numbers: frozenset[int]) -> str:
    """Turns dN tokens into anchors to the numbered stories; unknown numbers stay text.
    The input is escaped first, so the only markup in the result is ours."""
    from markupsafe import escape

    def replace(match: re.Match[str]) -> str:
        number = int(match.group(1))
        if number not in numbers:
            return match.group(0)
        return f'<sup><a class="cite" href="#story-{number}">{number}</a></sup>'

    return CITATION.sub(replace, str(escape(text)))
