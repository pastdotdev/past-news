"""`newsroom serve`, `newsroom fetch`, `newsroom ask`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date

import httpx

from .config import Config, ConfigError, load_config
from .fetcher import FetchState, run_once
from .ingest import NotSettled, wait_settled
from .past import PastClient
from .reader import ask


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="newsroom", description="A news aggregator that remembers through past."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the reader with the fetch loop inside it")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--no-fetch",
        action="store_true",
        help="serve without the fetch loop (run `newsroom fetch` from cron)",
    )

    fetch = commands.add_parser("fetch", help="pull every feed once and push the articles")
    fetch.add_argument("--topic", help="only this topic slug")
    fetch.add_argument(
        "--wait",
        action="store_true",
        help="return only once past has processed the new articles and they are readable",
    )

    question = commands.add_parser("ask", help="ask one topic a question")
    question.add_argument("topic")
    question.add_argument("question")
    question.add_argument(
        "--as-of", type=date.fromisoformat, help="read the topic as of this day (YYYY-MM-DD)"
    )

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        config = load_config()
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.command == "serve":
        import uvicorn

        from .web import create_app

        uvicorn.run(create_app(config, fetch_loop=not args.no_fetch), host=args.host, port=args.port)
        return 0

    if args.command == "fetch":
        if args.topic and config.topic(args.topic) is None:
            print(f"error: no topic {args.topic!r}", file=sys.stderr)
            return 2
        return asyncio.run(_fetch(config, args.topic, args.wait))

    if config.topic(args.topic) is None:
        print(f"error: no topic {args.topic!r}", file=sys.stderr)
        return 2
    return asyncio.run(_ask(config, args.topic, args.question, args.as_of))


async def _fetch(config: Config, only: str | None, wait: bool) -> int:
    state = FetchState()
    failed = 0
    async with httpx.AsyncClient(timeout=60) as http:
        await run_once(config, http, state, only=only)
        for slug, run in state.runs.items():
            counts = f"{run.fetched} fetched, {run.kept} kept, {run.changed} new, {run.unchanged} unchanged"
            print(f"{slug}: {counts}")
            for error in run.errors:
                print(f"  ! {error}")
                failed += 1
        if wait:
            failed += await _wait(config, state, http)
    return 1 if failed else 0


async def _wait(config: Config, state: FetchState, http: httpx.AsyncClient) -> int:
    failed = 0
    for slug, run in state.runs.items():
        if run.changed == 0:
            continue
        topic = config.topic(slug)
        assert topic is not None  # runs are keyed by configured topics
        print(f"{slug}: waiting for past to process {run.changed} new article(s)...", end="", flush=True)
        try:
            await wait_settled(PastClient(topic.api_key, config.base_url, http), run.ingestion_ids)
            print(" ready")
        except NotSettled as error:
            print(f"\n  ! {error}")
            failed += 1
    return failed


async def _ask(config: Config, slug: str, question: str, day: date | None) -> int:
    topic = config.topic(slug)
    assert topic is not None  # checked by main
    async with httpx.AsyncClient(timeout=120) as http:
        client = PastClient(topic.api_key, config.base_url, http)
        article = await ask(client, topic, config.identity, question, day)
    if article.text:
        print(article.text)
        print()
    else:
        print("(no answerer on this deployment; evidence only)\n")
    for story in article.stories:
        marker = "*" if story.cited else " "
        first_line = story.headline or (story.body.splitlines()[0] if story.body else "")
        print(f"{marker} d{story.number}  {story.occurred_at[:10]}  {first_line[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
