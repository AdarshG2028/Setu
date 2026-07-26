"""End-to-end tests for POST /videos and GET /videos/{id}.

Runs the app's real lifespan (Kafka producer + outbox publisher), so both
Postgres and Kafka must be reachable; skips cleanly otherwise — same
pattern as test_jobs_api.py.
"""

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.models import IdempotencyKey, Job, OutboxEvent, Result, Video, WorkerExecution

pytestmark = pytest.mark.usefixtures("database_url", "kafka_bootstrap_servers")

# `client` is a session-scoped fixture shared across every API test module —
# see its definition in conftest.py for why it can't be module-scoped here.


@pytest.fixture
async def cleanup_video_ids(database_url: str):
    created: list[uuid.UUID] = []
    yield created
    if not created:
        return
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.connect() as conn:
        for video_id in created:
            job_id = await conn.scalar(
                sa.select(Video.latest_analysis_job_id).where(Video.id == video_id)
            )
            await conn.execute(sa.delete(Video).where(Video.id == video_id))
            if job_id is not None:
                await conn.execute(
                    sa.delete(WorkerExecution).where(WorkerExecution.job_id == job_id)
                )
                await conn.execute(sa.delete(Result).where(Result.job_id == job_id))
                await conn.execute(
                    sa.delete(OutboxEvent).where(OutboxEvent.aggregate_id == job_id)
                )
                await conn.execute(
                    sa.delete(IdempotencyKey).where(IdempotencyKey.job_id == job_id)
                )
                await conn.execute(sa.delete(Job).where(Job.id == job_id))
        await conn.commit()
    await engine.dispose()


def _upload(client: TestClient, *, filename: str = "clip.mp4", data: bytes = b"fake video bytes"):
    return client.post("/videos", files={"file": (filename, data, "video/mp4")})


def test_upload_returns_201_with_video_and_job_id(
    client: TestClient, cleanup_video_ids: list
) -> None:
    response = _upload(client)
    assert response.status_code == 201
    body = response.json()
    cleanup_video_ids.append(uuid.UUID(body["video_id"]))

    assert body["status"] == "analyzing"
    uuid.UUID(body["job_id"])  # doesn't raise


def test_get_video_returns_analyzing_before_analysis_completes(
    client: TestClient, cleanup_video_ids: list
) -> None:
    uploaded = _upload(client).json()
    cleanup_video_ids.append(uuid.UUID(uploaded["video_id"]))

    response = client.get(f"/videos/{uploaded['video_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["original_filename"] == "clip.mp4"
    assert body["status"] in ("analyzing", "analyzed", "failed")
    # Whichever it is depends on whether a video_analysis worker happens to
    # be running against this broker right now — this test only proves the
    # lookup wiring, not the worker itself (see test_video_analysis_worker.py
    # for that, and the crash-recovery test pattern for a real worker run).


def test_get_unknown_video_is_404(client: TestClient) -> None:
    response = client.get(f"/videos/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_upload_creates_job1_event_published_end_to_end(
    client: TestClient, cleanup_video_ids: list, database_url: str
) -> None:
    """The full chain: POST /videos -> videos row + Job #1 -> outbox row ->
    publisher -> Kafka, mirroring test_jobs_api.py's equivalent for /jobs."""
    uploaded = _upload(client).json()
    video_id = uuid.UUID(uploaded["video_id"])
    job_id = uuid.UUID(uploaded["job_id"])
    cleanup_video_ids.append(video_id)

    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.connect() as conn:
        status_value = None
        for _ in range(20):
            status_value = (
                await conn.execute(
                    sa.select(OutboxEvent.status).where(OutboxEvent.aggregate_id == job_id)
                )
            ).scalar_one()
            if status_value == "published":
                break
            await asyncio.sleep(0.5)
        assert status_value == "published"
    await engine.dispose()
