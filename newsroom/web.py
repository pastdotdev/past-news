"""The reader: a topic list and one page per topic, server-rendered."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from .config import Config
from .fetcher import FetchState, run_forever, run_once
from .past import PastApiError, PastClient
from .reader import Article, Story, ask, briefing, render_citations, timeline

log = logging.getLogger(__name__)
HERE = Path(__file__).parent


@dataclass
class Services:
    config: Config
    http: httpx.AsyncClient
    state: FetchState

    def client(self, slug: str) -> PastClient:
        topic = self.config.topic(slug)
        if topic is None:
            raise HTTPException(status_code=404, detail="No such topic.")
        return PastClient(topic.api_key, self.config.base_url, self.http)


def create_app(config: Config, *, http: httpx.AsyncClient | None = None, fetch_loop: bool = True) -> FastAPI:
    services = Services(config=config, http=http or httpx.AsyncClient(timeout=60), state=FetchState())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(run_forever(config, services.http, services.state)) if fetch_loop else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
            await services.http.aclose()

    app = FastAPI(title="past newsroom", lifespan=lifespan)
    app.state.services = services
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["day"] = format_day
    templates.env.filters["ago"] = time_ago

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            {"topics": config.topics, "state": services.state, "now": datetime.now(UTC)},
        )

    @app.get("/t/{slug}", response_class=HTMLResponse)
    async def topic_page(request: Request, slug: str, as_of: str = "", q: str = "") -> HTMLResponse:
        topic = config.topic(slug)
        if topic is None:
            raise HTTPException(status_code=404, detail="No such topic.")
        day = parse_day(as_of)
        client = services.client(slug)
        question = q.strip()
        error: str | None = None
        lead: Article | None = None
        stories: tuple[Story, ...] = ()
        try:
            if question:
                lead, stories = await asyncio.gather(
                    ask(client, topic, config.identity, question, day),
                    timeline(client, topic, config.identity, day),
                )
            else:
                lead, stories = await asyncio.gather(
                    briefing(client, topic, config.identity, day),
                    timeline(client, topic, config.identity, day),
                )
        except PastApiError as api_error:
            error = describe(api_error)
        except httpx.HTTPError as http_error:
            error = f"past could not be reached: {http_error}"
        numbers = frozenset(story.number for story in lead.stories) if lead else frozenset()
        return templates.TemplateResponse(
            request,
            "topic.html",
            {
                "topics": config.topics,
                "topic": topic,
                "day": day,
                "as_of": as_of,
                "question": question,
                "lead": lead,
                "lead_html": Markup(render_citations(lead.text, numbers)) if lead and lead.text else None,
                "stories": stories,
                "error": error,
                "run": services.state.last(slug),
                "now": datetime.now(UTC),
            },
        )

    @app.post("/fetch")
    async def fetch_now(slug: str = Form("")) -> RedirectResponse:
        only = slug or None
        asyncio.create_task(run_once(config, services.http, services.state, only=only))
        return RedirectResponse(url=f"/t/{slug}" if slug else "/", status_code=303)

    return app


def parse_day(value: str) -> date | None:
    if not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as error:
        raise HTTPException(status_code=400, detail="as_of must be a date like 2026-09-08.") from error


def describe(error: PastApiError) -> str:
    if error.status == 401:
        return "past refused this topic's project key. Check the variable named in topics.yaml."
    if error.status == 403:
        return "This topic's project is archived."
    return f"past answered {error.status} ({error.code}): {error}"


def format_day(value: str | date | datetime) -> str:
    if not isinstance(value, str):
        return value.strftime("%-d %b %Y")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%-d %b %Y")
    except ValueError:
        return value


def time_ago(value: datetime, now: datetime) -> str:
    seconds = int((now - value).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"
