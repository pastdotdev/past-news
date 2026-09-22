import pytest

from newsroom.config import ConfigError, parse_config

VALID = """
fetch_every_minutes: 5
topics:
  - slug: ai-regulation
    name: AI regulation
    api_key_env: KEY_AI
    feeds: [https://a/feed, https://b/feed]
    keywords: [" AI Act ", regulation]
  - slug: chips
    name: Chips
    api_key_env: KEY_CHIPS
    feeds: [https://c/feed]
"""


def test_parses_topics_and_reads_keys_from_the_named_variables() -> None:
    config = parse_config(
        VALID, {"KEY_AI": "past_sk_a", "KEY_CHIPS": "past_sk_c", "PAST_BASE_URL": "https://p/"}
    )

    assert config.base_url == "https://p"
    assert config.identity == "newsroom"
    assert config.fetch_every_minutes == 5
    assert [t.slug for t in config.topics] == ["ai-regulation", "chips"]
    assert config.topics[0].api_key == "past_sk_a"
    assert config.topics[0].keywords == ("AI Act", "regulation")
    assert config.topics[1].keywords == ()
    assert config.topic("chips") is config.topics[1]
    assert config.topic("nope") is None


def test_missing_key_names_the_variable() -> None:
    with pytest.raises(ConfigError, match="KEY_CHIPS is not set"):
        parse_config(VALID, {"KEY_AI": "past_sk_a"})


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("topics: []", "declares no topics"),
        ("topics:\n  - slug: Bad Slug\n    name: x\n    api_key_env: K\n    feeds: [f]", "lowercase"),
        ("topics:\n  - slug: a\n    name: x\n    api_key_env: K\n    feeds: []", "feeds must be"),
        (
            "fetch_every_minutes: 0\ntopics:\n  - slug: a\n    name: x\n    api_key_env: K\n    feeds: [f]",
            "positive",
        ),
        (
            "topics:\n"
            "  - {slug: a, name: x, api_key_env: K, feeds: [f]}\n"
            "  - {slug: a, name: y, api_key_env: K, feeds: [f]}",
            "Duplicate",
        ),
    ],
)
def test_rejects_bad_files(text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(text, {"K": "past_sk"})
