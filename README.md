# Argus — See everything. Understand anything.

A distributed, async data pipeline for scraping web articles, processing them through an LLM for structured analysis, and presenting insights through a real-time dashboard.

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
- **Frontend** — Vanilla HTML/CSS/JS dashboard with real-time polling

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
│   ├── services/
│   │   ├── cache.py         # Redis URL cache with LRU eviction
│   │   ├── llm_processor.py # LLM integration service
│   │   └── scraper.py       # HTTP scraping + HTML parsing
│   └── tasks/
│       ├── scraping.py      # Celery scrape tasks
│       └── processing.py    # Celery LLM processing tasks
├── frontend/
│   ├── index.html           # Dashboard markup
│   ├── styles.css           # Dark-theme responsive styles
│   └── app.js               # Client-side logic & polling
├── docker-compose.yml       # Full stack orchestration
├── Dockerfile               # Python app container
├── requirements.txt         # Python dependencies
└── .env.example             # Environment variable template
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | Async PostgreSQL connection | `postgresql+asyncpg://...` |
| `REDIS_URL` | Redis for caching | `redis://localhost:6379/0` |
| `CELERY_BROKER_URL` | Celery message broker | `redis://localhost:6379/1` |
| `LLM_API_KEY` | API key for LLM provider | — |
| `LLM_API_URL` | LLM endpoint URL | OpenAI chat completions |
| `LLM_MODEL` | Model identifier | `gpt-4o-mini` |
