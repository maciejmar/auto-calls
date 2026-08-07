import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import session as db_session_module
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import call, enquiry, tenant  # noqa: F401 - register models on Base.metadata


@pytest.fixture(autouse=True)
def webhook_secret(monkeypatch):
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", "test-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def db_engine():
    # StaticPool keeps every session on the same underlying connection, so
    # the in-memory sqlite database survives across the multiple independent
    # sessions opened per request (both by the app's get_db override and by
    # tests asserting on db_session directly).
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_engine, monkeypatch):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    # BackgroundTasks (e.g. extraction_service.process_call) open their own
    # session via app.db.session.async_session_factory rather than through
    # the get_db dependency, so route that at the module level too.
    monkeypatch.setattr(db_session_module, "async_session_factory", session_factory)

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.pop(get_db, None)
