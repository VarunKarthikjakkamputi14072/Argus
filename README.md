# Argus — See everything. Understand anything.

A distributed, async pipeline that scrapes web articles, runs them through an LLM for
structured analysis (summary, entities, sentiment), and streams the results to a live
dashboard.

I built this to work through the moving parts of a real task-queue system — backpressure,
retries, dead-letter handling, deduplication, and pushing live updates to a browser —
rather than just calling an LLM in a request handler. The section below on design
decisions explains the choices behind it.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  Frontend   │────▶│  FastAPI     │────▶│  Celery Workers │
│  Dashboard  │◀────│  API Layer   │◀────│  (3 instances)  │
└─────────────┘     └──────────────┘     └─────────────────┘
                           │                      │
                    ┌──────┴──────┐        ┌──────┴──────┐
                    │ PostgreSQL  │        │    Redis     │
                    │ (articles + │        │ (broker +   │
                    │  metadata)  │        │  URL cache) │
                    └─────────────┘        └─────────────┘
```

### Components

- **FastAPI** — REST API for URL submission, article queries, and queue monitoring
- **Celery** — Distributed task queue with 3 workers simulating load-balanced processing
- **Redis** — Message broker + application-level LRU URL cache with configurable eviction
- **PostgreSQL** — Persistent storage for articles and LLM-extracted metadata
- **LLM Service** — Processes article text to extract summaries, entities, and sentiment
- **Frontend** — Vanilla HTML/CSS/JS dashboard, updated live over Server-Sent Events (with polling as a fallback)

## Design decisions

A few choices I made on purpose, and why:

- **Two Celery queues, not one.** Scraping is I/O-bound and network-flaky; LLM processing
  is slower and costs money per call. Splitting them into `scraping` and `processing`
  queues means I can scale or rate-limit each independently, and a backlog of slow LLM
  calls never blocks fresh scrapes.
- **`acks_late` + `prefetch=1`.** Tasks are acknowledged only after they finish, so if a
  worker dies mid-task the job goes back on the queue instead of vanishing. Prefetch of 1
  stops a single worker from hoarding messages it can't get to — simple backpressure that
  keeps work spread across workers.
- **A dead-letter queue.** After a task exhausts its retries it's handed to a
  `dead_letter` queue and the article is marked failed with the reason, instead of the
  failure disappearing into the logs. It's the difference between "something broke" and
  "this URL broke because X".
- **Two-tier URL cache.** Deduplication runs at the app level (a Redis sorted set scored
  by last access, oldest 10% evicted in a batch when it's full) on top of Redis's own
  `allkeys-lru`. The app-level tier lets me decide *what* survives eviction rather than
  leaving it entirely to the server; the server policy is just a safety net.
- **Idempotent submission.** The URL column is unique and the submit endpoint checks for
  an existing article, so resubmitting the same URL returns 409 instead of scraping it
  twice. The cache then short-circuits re-processing if the content is already there.
- **The LLM can fail without taking the pipeline down.** A circuit breaker trips after
  repeated LLM errors, and there's a heuristic fallback (regex-based summary, entity, and
  sentiment extraction) so the system keeps producing output — and so the whole thing
  runs locally with no API key.
- **trafilatura for extraction.** I started with a hand-rolled BeautifulSoup pass over
  `<p>` tags, which pulls in nav/ads/boilerplate. trafilatura does a much better job of
  isolating the actual article body; the BeautifulSoup path is kept as a fallback for
  pages it can't parse. The scraper also checks `robots.txt` before fetching.
- **Live updates over SSE, not polling.** Workers publish status changes to a Redis
  channel; the API subscribes and forwards them to the browser over Server-Sent Events.
  Because the workers and the API are separate processes, Redis pub/sub is what lets the
  API "see" what the workers are doing. The dashboard falls back to polling if the stream
  drops.

## Quick Start

```bash
# Clone and configure
cp .env.example .env
# Edit .env with your LLM API key

# Start all services
docker-compose up --build

# Access the dashboard
open http://localhost:8000
```

The LLM API key is optional — without one, the analysis step uses the built-in heuristic
fallback, so the full pipeline still runs end to end.

## Deploying to Fly.io

Argus is a multi-process app — a FastAPI web server, Celery workers, and a beat
scheduler — so it can't run on a static host or a single serverless function. Fly.io
runs the Docker image as several **process groups** (defined in `fly.toml`) backed by
managed Redis and Postgres. The FastAPI app serves the dashboard at `/`, so the whole
demo lives behind one URL.

```bash
# 1. Install + sign in
brew install flyctl        # or: curl -L https://fly.io/install.sh | sh
fly auth login

# 2. Create the app (pick a globally unique name; update `app` in fly.toml to match)
fly apps create argus-pipeline

# 3. Provision managed data stores
fly postgres create --name argus-db --region iad
fly postgres attach argus-db --app argus-pipeline   # sets DATABASE_URL automatically
fly redis create                                    # Upstash Redis; note the rediss:// URL

# 4. Wire up secrets. Postgres attach gives you a postgres:// URL — Argus needs the
#    async (asyncpg) form for the API and the sync (psycopg2) form for the workers.
fly secrets set \
  DATABASE_URL="postgresql+asyncpg://<user>:<pass>@<host>:5432/<db>" \
  SYNC_DATABASE_URL="postgresql://<user>:<pass>@<host>:5432/<db>" \
  REDIS_URL="rediss://<upstash-host>:6379/0" \
  CELERY_BROKER_URL="rediss://<upstash-host>:6379/1" \
  CELERY_RESULT_BACKEND="rediss://<upstash-host>:6379/2" \
  LLM_API_KEY="<optional — omit to use the heuristic fallback>"

# 5. Ship it (builds the Dockerfile, boots web + worker + beat machines)
fly deploy

# 6. Open the live dashboard
fly open
```

Notes:
- **Redis databases.** Upstash gives one logical DB; if you only get DB `0`, point all
  three URLs at it — the broker/cache/result keys don't collide in practice. The
  separate `/0` `/1` `/2` split in local Compose is just for tidiness.
- **Scaling workers.** `fly scale count worker=3` runs three worker machines, matching
  the three-worker Compose setup. `web` and `beat` should stay at 1.
- **`min_machines_running = 1`** keeps the web tier warm so the demo never cold-starts on
  a recruiter's click. Drop it to `0` to let Fly stop idle machines and save cost.
- This is a different shape from your static/Vercel projects — those have no persistent
  background workers or broker, which is exactly why Argus needs a container host.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/articles/submit` | Submit a URL for processing |
| POST | `/api/articles/submit-batch` | Submit multiple URLs |
| GET | `/api/articles` | List articles (filterable) |
| GET | `/api/articles/{id}` | Get article detail + metadata |
| GET | `/api/insights` | Get LLM-processed insights |
| GET | `/api/queue/status` | Celery queue statistics |
| GET | `/api/queue/tasks` | In-flight task list |
| GET | `/api/stats` | Platform-wide statistics |
| GET | `/api/events` | Server-Sent Events stream of live status changes |

## Cache Eviction Policy

The URL deduplication cache uses a two-tier strategy:

1. **Application-level LRU** — URLs stored in a Redis sorted set keyed by last-access timestamp. When the set exceeds `REDIS_MAX_URLS` (default: 100,000), the oldest 10% are evicted in a batch operation.
2. **Server-level fallback** — Redis `maxmemory-policy` is set to `allkeys-lru` (256MB limit), providing a safety net if application-level eviction falls behind.

## Project Structure

```
├── backend/
│   ├── main.py              # FastAPI application entry
│   ├── config.py            # Pydantic settings
│   ├── celery_app.py        # Celery configuration & routing
│   ├── api/routes.py        # API endpoint definitions
│   ├── db/session.py        # SQLAlchemy engine & sessions
│   ├── models/database.py   # ORM models (Article, ArticleMetadata)
│   ├── models/types.py      # Portable column types (Postgres + SQLite)
│   ├── services/
│   │   ├── cache.py         # Redis URL cache with LRU eviction
│   │   ├── events.py        # Redis pub/sub helper for live updates
│   │   ├── llm_processor.py # LLM integration + heuristic fallback
│   │   └── scraper.py       # trafilatura extraction + robots.txt + BS4 fallback
│   └── tasks/
│       ├── scraping.py      # Celery scrape tasks
│       ├── processing.py    # Celery LLM processing tasks
│       └── dlq.py           # Dead-letter handler
├── frontend/
│   ├── index.html           # Dashboard markup
│   ├── styles.css           # Dark-theme responsive styles
│   └── app.js               # Client-side logic (SSE + polling fallback)
├── tests/                   # pytest suite (SQLite + fakeredis, no services needed)
├── .github/workflows/ci.yml # Lint + tests on push/PR
├── docker-compose.yml       # Full stack orchestration
├── Dockerfile               # Python app container
├── requirements.txt         # Python dependencies
├── requirements-dev.txt     # Test/lint dependencies
└── .env.example             # Environment variable template
```

## Tests

The suite runs against in-memory SQLite and fakeredis, with the LLM in heuristic-fallback
mode, so it needs no Postgres, Redis, or broker running:

```bash
pip install -r requirements-dev.txt
ruff check backend tests
pytest
```

CI (`.github/workflows/ci.yml`) runs the same lint + tests on Python 3.11 and 3.12 for
every push and pull request.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | Async PostgreSQL connection | `postgresql+asyncpg://...` |
| `REDIS_URL` | Redis for caching | `redis://localhost:6379/0` |
| `CELERY_BROKER_URL` | Celery message broker | `redis://localhost:6379/1` |
| `LLM_API_KEY` | API key for LLM provider | — |
| `LLM_API_URL` | LLM endpoint URL | OpenAI chat completions |
| `LLM_MODEL` | Model identifier | `gpt-4o-mini` |

## What I'd do next

Things I'd reach for as the system grows, roughly in priority order:

- **Semantic search over articles.** Embed each article and store the vectors so you can
  search by meaning, not just keyword `ILIKE`.
- **Near-duplicate detection.** The URL cache stops the *same* link being processed twice,
  but the same story reposted on three sites still goes through three times. Embeddings +
  a similarity threshold would catch those.
- **Per-domain politeness/rate limiting.** Right now I honor `robots.txt`; I'd add a
  per-domain crawl delay so a batch of URLs from one site doesn't hammer it.
- **Push the SSE feed through a fan-out layer** (or move to WebSockets) once there are
  enough concurrent dashboard clients that a pub/sub subscription per connection stops
  being the simplest option.
- **Surface the dead-letter queue in the UI** with a one-click requeue, instead of only
  recording failures in the database.
