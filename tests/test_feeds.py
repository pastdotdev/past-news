from datetime import UTC, datetime
from pathlib import Path

from newsroom.feeds import Article, canonical_url, matches_keywords, parse_feed

FEED = (Path(__file__).parent / "fixtures" / "feed.xml").read_text()
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def test_parse_feed_reads_entries_newest_first_and_skips_linkless() -> None:
    articles = parse_feed(FEED, "https://example.tech/feed", now=NOW)

    assert [a.title for a in articles] == [
        "Undated item",
        "A review of the new kettle",
        "EU lawmakers agree on AI Act & enforcement timeline",
    ]
    assert all(a.outlet == "Example Tech" for a in articles)
    assert all(a.feed_url == "https://example.tech/feed" for a in articles)


def test_parse_feed_cleans_html_and_canonicalises_the_link() -> None:
    article = parse_feed(FEED, "https://example.tech/feed", now=NOW)[-1]

    assert article.url == "https://example.tech/ai-act-timeline"
    assert article.summary == "The European Parliament and member states settled the enforcement schedule."
    assert article.published == datetime(2026, 9, 7, 8, 30, tzinfo=UTC)


def test_undated_entry_is_stamped_with_now() -> None:
    article = parse_feed(FEED, "https://example.tech/feed", now=NOW)[0]
    assert article.published == NOW


def test_canonical_url_drops_tracking_and_fragment_but_keeps_real_query() -> None:
    assert (
        canonical_url("HTTPS://Example.com/a?id=3&utm_campaign=x&fbclid=y#frag")
        == "https://example.com/a?id=3"
    )
    assert canonical_url("https://example.com") == "https://example.com/"


def test_keywords_match_title_or_summary_case_insensitively() -> None:
    article = Article(
        url="https://x/1",
        title="Chip export rules",
        summary="TSMC responds.",
        published=NOW,
        outlet="X",
        feed_url="f",
    )
    assert matches_keywords(article, ())
    assert matches_keywords(article, ("tsmc",))
    assert matches_keywords(article, ("EXPORT",))
    assert not matches_keywords(article, ("regulation", "AI Act"))
