import asyncio

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
