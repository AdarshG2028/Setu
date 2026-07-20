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
    """A session rolled back after each test, so cases stay independent."""
    engine = create_async_engine(database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
        await s.rollback()
    await engine.dispose()
