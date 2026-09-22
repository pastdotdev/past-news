# past-news

A news aggregator with a memory. Each topic you follow is one [past](https://past.dev)
project: the newsroom pulls the topic's RSS feeds, pushes every article in as a dated data
point, and reads the topic back as a written briefing, a timeline, and answers to your
questions. Pick a date and the whole page rewrites itself as of that day.

No news API, no scraping, no database. Feeds in, past remembers, the reader asks.

This is a showcase for the past Memory API, kept small on purpose: one fetch loop, one
typed client, one FastAPI reader. Fork it, list the feeds you care about, and you have a
newsroom that remembers everything they published.

## Run it

You need one **project key** per topic from your past console (Settings › API keys). A project
key reads and writes exactly one project. Never use the organization's management key here.

```sh
cp topics.example.yaml topics.yaml   # your topics and their feeds
cp .env.example .env                 # keys and past URL; then: set -a; source .env; set +a
uv sync
uv run newsroom fetch --wait         # pull every feed once and wait until past has read it
uv run newsroom serve                # http://127.0.0.1:8000
```

`serve` fetches every feed on start and then every `fetch_every_minutes`. For a cron-driven
setup run `newsroom serve --no-fetch` and schedule `newsroom fetch` separately.

```sh
uv run newsroom fetch                         # pull every feed once, print what changed
uv run newsroom fetch --topic chips --wait    # one topic, and wait for the new articles
uv run newsroom ask chips "what did TSMC announce?" --as-of 2026-09-01
```

The first fetch on a fresh project takes a few minutes to settle: past reads each article,
works out what it says and how it relates to what it already knows. `--wait` returns when
that is done and the briefing has something to say.

### Configuration

`topics.yaml` (committed in your fork; it holds feed URLs and variable names, never keys):

```yaml
fetch_every_minutes: 15
topics:
  - slug: ai-regulation          # in the URL: /t/ai-regulation
    name: AI regulation
    api_key_env: NEWSROOM_KEY_AI_REGULATION   # the env var holding this topic's project key
    feeds:
      - https://www.theverge.com/rss/ai-artificial-intelligence/index.xml
    keywords: [regulation, "AI Act"]           # optional filter on title + summary
```

| Variable          | Default                | What it does                                                       |
| ----------------- | ---------------------- | ------------------------------------------------------------------ |
| `PAST_BASE_URL`   | `https://api.past.dev` | Your self-hosted URL if you run past yourself.                     |
| `PAST_IDENTITY`   | `newsroom`             | Who the reads answer as.                                           |
| `NEWSROOM_TOPICS` | `topics.yaml`          | Path to the topics file.                                           |
| `NEWSROOM_KEY_*`  | required               | One per topic, named in `topics.yaml` under `api_key_env`.         |

### Fetching from GitHub Actions

`.github/workflows/fetch.yml` runs `newsroom fetch` every thirty minutes, so a fork keeps its
topics fed with no server of its own. Set it up once:

1. Commit your `topics.yaml`.
2. Add one repository secret per topic, named exactly as its `api_key_env`.
3. List each of those secrets under `env:` in the workflow. GitHub passes a workflow only the
   secrets it names, so a new topic is one line there.
4. If you self-host past, add the repository variable `PAST_BASE_URL`.

Then run `newsroom serve --no-fetch` wherever you like, or just use `newsroom ask` from a
terminal. The reader never needs to be the process that fetches.

## How it works

```text
feeds ──fetch──▶ articles ──▶ data points ──POST /api/v1/ingest/batch──▶ past (one project per topic)
                                                                              │
reader ◀── briefing (POST /api/v1/answer) ◀───────────────────────────────────┤
       ◀── timeline (POST /api/v1/recall, sort=chronological) ◀───────────────┤
       ◀── ask      (POST /api/v1/answer) ◀───────────────────────────────────┘
                     all with queryTimestamp = the chosen day
```

- **An article is a data point.** Its id is the canonical URL (tracking parameters and
  fragments stripped), its timestamp is the publish date, its content is the headline, the
  feed's summary, and a last line naming the outlet and link. Re-pushing an article past
  already holds is a no-op on past's side, so every fetch pushes everything and past reports
  what was new.
- **`--wait` polls `GET /api/v1/ingest/{id}`** for each push until it is settled, which is
  when the articles are readable through recall and answer.
- **The briefing is `/answer`** with an instruction to write newest-first paragraphs, each
  ending in its citation. `dN` citations become links to the numbered sources under the text.
- **The timeline is `/recall`** for the topic, presented chronologically and flipped to
  newest first. Source articles show their headline and outlet; memories past consolidated
  from several articles show as plain statements.
- **"As of" is `queryTimestamp`.** The date picker sets it to the end of that day in UTC, so
  briefing, timeline and answers all read the project as it stood then.
- A self-hosted past with no answer model refuses `/answer`; the reader then shows the recall
  evidence in place of the written text.

## Deploy it

`newsroom serve --host 0.0.0.0 --port 8000` is a plain uvicorn process; run it anywhere
Python 3.12 runs, with the environment variables above. With the fetch workflow doing the
ingestion, the server holds no state at all and can be restarted at will.

## Develop

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy newsroom
```

Tests run against an in-memory fake of the past API (`tests/fake_past.py`) mounted as an
`httpx` transport, and a fixture RSS feed. Nothing in the suite reaches the network.

## Layout

```text
newsroom/
  config.py     # topics.yaml + env, fails loudly
  past.py       # typed client: push, status, recall, answer
  feeds.py      # RSS/Atom -> Article (feedparser)
  ingest.py     # Article -> DataPoint, batched pushes, waiting for settlement
  fetcher.py    # one run over all topics; the loop
  reader.py     # briefing, ask, timeline, citations
  web.py        # FastAPI + Jinja2 reader
  cli.py        # newsroom serve | fetch | ask
  templates/, static/
tests/
.github/workflows/fetch.yml   # the scheduled fetch
```

MIT.
