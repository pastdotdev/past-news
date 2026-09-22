"""Reading RSS and Atom feeds into articles.

Feeds are the only news source: no API keys, no scraping. What a feed publishes (title, summary,
link, date) is what the newsroom remembers.
"""

from __future__ import annotations

import asyncio
import html
import re
from calendar import timegm
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}
TAG = re.compile(r"<[^>]+>")
SPACE = re.compile(r"\s+")
MAX_SUMMARY = 2000


@dataclass(frozen=True)
class Article:
    url: str
    title: str
    summary: str
    published: datetime
    outlet: str
    feed_url: str


def canonical_url(url: str) -> str:
    """Drops tracking parameters and fragments so one article has one id across feeds."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in TRACKING_PARAMS]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(query), ""))


def clean_text(value: str) -> str:
    return SPACE.sub(" ", html.unescape(TAG.sub(" ", value))).strip()


def parse_feed(document: str | bytes, feed_url: str, *, now: datetime | None = None) -> list[Article]:
    """Articles from one feed document, newest first. Entries without a link are skipped;
    entries without a date are stamped with `now`, because a data point needs a date."""
    parsed = feedparser.parse(document)
    outlet = clean_text(parsed.feed.get("title", "")) or urlsplit(feed_url).netloc
    fallback = now or datetime.now(UTC)
    articles: list[Article] = []
    for entry in parsed.entries:
        link = entry.get("link")
        if not link:
            continue
        title = clean_text(entry.get("title", ""))
        if not title:
            continue
        summary = clean_text(entry.get("summary", "") or _first_content(entry))[:MAX_SUMMARY]
        struct = entry.get("published_parsed") or entry.get("updated_parsed")
        # feedparser normalises dates to UTC struct_time; timegm reads it as UTC (mktime would
        # read it as local time and shift every date by the machine's offset).
        published = datetime.fromtimestamp(timegm(struct), tz=UTC) if struct else fallback
        articles.append(
            Article(
                url=canonical_url(link),
                title=title,
                summary=summary,
                published=published,
                outlet=outlet,
                feed_url=feed_url,
            )
        )
    articles.sort(key=lambda article: article.published, reverse=True)
    return articles


def matches_keywords(article: Article, keywords: tuple[str, ...]) -> bool:
    """No keywords keeps everything; otherwise the title or summary must contain one."""
    if not keywords:
        return True
    haystack = f"{article.title}\n{article.summary}".casefold()
    return any(word.casefold() in haystack for word in keywords)


async def fetch_feed(http: httpx.AsyncClient, feed_url: str) -> list[Article]:
    response = await http.get(feed_url, headers={"User-Agent": "past-news/0.1"}, follow_redirects=True)
    response.raise_for_status()
    # feedparser is synchronous and can be slow on big feeds; keep the event loop free.
    return await asyncio.to_thread(parse_feed, response.content, feed_url)


def _first_content(entry: feedparser.FeedParserDict) -> str:
    content = entry.get("content") or []
    return str(content[0].get("value", "")) if content else ""
