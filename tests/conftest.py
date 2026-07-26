import asyncio
import shutil

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.api.main import create_app
from backend.core.config import get_settings


def _database_reachable(url: str) -> bool:
    async def check() -> bool:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                await conn.execute(sa.text("SELECT 1"))
            return True
        except Exception:
            return False
        finally:
            await engine.dispose()

    try:
        return asyncio.run(check())
    except Exception:
        return False


@pytest.fixture(scope="session")
def database_url() -> str:
    url = str(get_settings().database_url)
    if not _database_reachable(url):
        pytest.skip(
            "Postgres not reachable — start it with "
            "`docker compose -f docker/docker-compose.yml up -d`",
            allow_module_level=True,
        )
    return url


@pytest.fixture
async def session(database_url: str):
    """A session whose commits are undone after the test.

    Bound to a connection-level transaction with join_transaction_mode=
    "create_savepoint": session.commit() only releases a SAVEPOINT and opens
    a new one, so code under test (like JobSubmissionService, which commits
    for real) still gets real commit semantics, but the outer rollback below
    erases everything once the test ends.
    """
    engine = create_async_engine(database_url)
    async with engine.connect() as conn:
        await conn.begin()
        maker = async_sessionmaker(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with maker() as s:
            yield s
        await conn.rollback()
    await engine.dispose()


def _kafka_reachable(bootstrap_servers: str) -> bool:
    from aiokafka import AIOKafkaProducer

    async def check() -> bool:
        producer = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
        try:
            await asyncio.wait_for(producer.start(), timeout=5)
            return True
        except Exception:
            return False
        finally:
            try:
                await producer.stop()
            except Exception:
                pass

    try:
        return asyncio.run(check())
    except Exception:
        return False


@pytest.fixture(scope="session")
def kafka_bootstrap_servers() -> str:
    servers = get_settings().kafka_bootstrap_servers
    if not _kafka_reachable(servers):
        pytest.skip(
            "Kafka not reachable — start it with "
            "`docker compose -f docker/docker-compose.yml up -d`",
            allow_module_level=True,
        )
    return servers


@pytest.fixture(scope="session")
def client():
    """One TestClient (one portal event loop) for the *entire test session*,
    shared by every module that hits the real app (test_jobs_api.py,
    test_videos_api.py, ...).

    backend.database.session.get_engine() is a process-wide singleton
    (@lru_cache), matching production where the app runs once. Two
    module-scoped clients — one per test file — would each spin up their
    own portal thread/event loop while both still draw connections from
    that one shared engine's pool: a connection opened under the first
    module's loop can get reused and torn down under the second module's
    different loop, which asyncpg/SQLAlchemy can't do ("RuntimeError: Event
    loop is closed", surfacing only once a second such module exists).
    Session-scoping this fixture in conftest.py means there is only ever
    one portal for the whole run, regardless of how many test files use it.
    """
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(scope="session")
def ffprobe_available() -> None:
    """VideoAnalysisWorker shells out to a real ffprobe binary — tests that
    exercise that need it installed and on PATH, same skip-cleanly pattern
    as the Postgres/Kafka fixtures above."""
    if shutil.which("ffprobe") is None:
        pytest.skip(
            "ffprobe not on PATH — install ffmpeg to run this test",
            allow_module_level=True,
        )
