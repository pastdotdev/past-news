"""Deployment configuration: the topics file plus a few environment variables.

Every topic names the environment variable that holds its project key. Nothing is inferred
from the slug, so a missing key is an error with the variable's name in it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_BASE_URL = "https://api.past.dev"
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Topic:
    slug: str
    name: str
    api_key: str
    feeds: tuple[str, ...]
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    base_url: str
    identity: str
    fetch_every_minutes: int
    topics: tuple[Topic, ...] = field(default_factory=tuple)

    def topic(self, slug: str) -> Topic | None:
        return next((topic for topic in self.topics if topic.slug == slug), None)


def load_config(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    path = Path(env.get("NEWSROOM_TOPICS", "topics.yaml"))
    if not path.exists():
        raise ConfigError(f"{path} not found. Copy topics.example.yaml to {path} and edit it.")
    return parse_config(path.read_text(encoding="utf-8"), env)


def parse_config(text: str, env: Mapping[str, str]) -> Config:
    raw = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise ConfigError("The topics file must be a mapping.")

    every = raw.get("fetch_every_minutes", 15)
    if not isinstance(every, int) or every < 1:
        raise ConfigError("fetch_every_minutes must be a positive integer.")

    entries = raw.get("topics")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("The topics file declares no topics.")

    topics = tuple(_parse_topic(entry, env) for entry in entries)
    slugs = [topic.slug for topic in topics]
    duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    if duplicates:
        raise ConfigError(f"Duplicate topic slug: {', '.join(duplicates)}")

    return Config(
        base_url=env.get("PAST_BASE_URL", DEFAULT_BASE_URL).rstrip("/") or DEFAULT_BASE_URL,
        identity=env.get("PAST_IDENTITY", "newsroom").strip() or "newsroom",
        fetch_every_minutes=every,
        topics=topics,
    )


def _parse_topic(entry: object, env: Mapping[str, str]) -> Topic:
    if not isinstance(entry, dict):
        raise ConfigError("Each topic must be a mapping.")
    slug = entry.get("slug")
    if not isinstance(slug, str) or not SLUG.match(slug):
        raise ConfigError(f"Topic slug {slug!r} must be lowercase words joined by dashes.")
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"Topic {slug}: name is required.")
    key_env = entry.get("api_key_env")
    if not isinstance(key_env, str) or not key_env:
        raise ConfigError(f"Topic {slug}: api_key_env is required.")
    api_key = env.get(key_env, "").strip()
    if not api_key:
        raise ConfigError(f"Topic {slug}: environment variable {key_env} is not set.")
    feeds = entry.get("feeds")
    if not isinstance(feeds, list) or not feeds or not all(isinstance(url, str) for url in feeds):
        raise ConfigError(f"Topic {slug}: feeds must be a non-empty list of URLs.")
    keywords = entry.get("keywords", [])
    if not isinstance(keywords, list) or not all(isinstance(word, str) for word in keywords):
        raise ConfigError(f"Topic {slug}: keywords must be a list of strings.")
    return Topic(
        slug=slug,
        name=name.strip(),
        api_key=api_key,
        feeds=tuple(feeds),
        keywords=tuple(word.strip() for word in keywords if word.strip()),
    )
