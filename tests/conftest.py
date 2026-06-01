"""Shared test fixtures.

The suite is self-contained: an in-memory SQLite DB stands in for Postgres and
fakeredis stands in for Redis, so `pytest` runs with no services up. The LLM
isn't called either — with no API key configured, llm_processor uses its
heuristic fallback.
"""

import fakeredis
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.api.routes import router
from backend.db.session import get_db
from backend.models.database import Base


@pytest_asyncio.fixture
async def db_session():
    """An isolated in-memory database, fresh per test."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session):
    """An HTTP client wired to the API router with the test DB injected."""
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def _get_db():
        # Mirror the real dependency: commit on success, roll back on error.
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_db] = _get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def fake_redis():
    """A fakeredis client (decode_responses=True, like the real one)."""
    return fakeredis.FakeStrictRedis(decode_responses=True)
