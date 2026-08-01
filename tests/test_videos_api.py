"""End-to-end tests for POST /projects/{id}/videos and GET /videos/{id}.

Runs the app's real lifespan (Kafka producer + outbox publisher), so both
Postgres and Kafka must be reachable; skips cleanly otherwise — same
pattern as test_jobs_api.py. Every video now belongs to a project (see
backend/models/video.py), so each test creates one first.
"""

import asyncio
import uuid

import pytest

from tests.conftest import as_user
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.models import IdempotencyKey, Job, OutboxEvent, Project, Result, Video, WorkerExecution

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


@pytest.fixture
async def cleanup_project_ids(database_url: str):
    created: list[uuid.UUID] = []
    yield created
    if not created:
        return
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.connect() as conn:
        # Videos in cleanup_video_ids are already gone by the time this
        # runs (fixture teardown order is LIFO); this only ever deletes an
        # empty project.
        for project_id in created:
            await conn.execute(sa.delete(Project).where(Project.id == project_id))
        await conn.commit()
    await engine.dispose()


def _create_project(client: TestClient) -> uuid.UUID:
    """Returns the project id, recording its owner in _OWNERS so later
    calls can send a membership-satisfying header without every caller
    threading the owner through."""
    owner_id = uuid.uuid4()
    response = client.post("/projects", json={}, headers=as_user(owner_id))
    assert response.status_code == 201
    body = response.json()
    project_id = uuid.UUID(body["id"])
    _OWNERS[project_id] = uuid.UUID(body["owner_id"])
    return project_id


# project id -> its owner, so _upload can send a membership-satisfying
# header without every caller having to thread the owner through.
_OWNERS: dict[uuid.UUID, uuid.UUID] = {}


def _upload(
    client: TestClient,
    project_id: uuid.UUID,
    *,
    filename: str = "clip.mp4",
    data: bytes = b"fake video bytes",
    name: str | None = None,
):
    return client.post(
        f"/projects/{project_id}/videos",
        files={"file": (filename, data, "video/mp4")},
        data={"name": name} if name is not None else None,
        headers=as_user(_OWNERS.get(project_id, project_id)),
    )


def test_upload_returns_201_with_video_and_job_id(
    client: TestClient, cleanup_project_ids: list, cleanup_video_ids: list
) -> None:
    project_id = _create_project(client)
    cleanup_project_ids.append(project_id)

    response = _upload(client, project_id)
    assert response.status_code == 201
    body = response.json()
    cleanup_video_ids.append(uuid.UUID(body["video_id"]))

    assert body["project_id"] == str(project_id)
    assert body["status"] == "analyzing"
    assert body["name"] is None
    uuid.UUID(body["job_id"])  # doesn't raise


def test_upload_with_display_name_persists_and_is_readable(
    client: TestClient, cleanup_project_ids: list, cleanup_video_ids: list
) -> None:
    project_id = _create_project(client)
    cleanup_project_ids.append(project_id)

    uploaded = _upload(client, project_id, filename="raw_export_final_v3.mp4", name="Intro clip")
    assert uploaded.status_code == 201
    body = uploaded.json()
    cleanup_video_ids.append(uuid.UUID(body["video_id"]))
    assert body["name"] == "Intro clip"

    detail = client.get(f"/videos/{body['video_id']}").json()
    assert detail["name"] == "Intro clip"
    assert detail["original_filename"] == "raw_export_final_v3.mp4"

    listed = client.get(f"/projects/{project_id}/videos", headers=as_user(_OWNERS[project_id])).json()["videos"]
    assert listed[0]["name"] == "Intro clip"


def test_upload_to_unknown_project_is_404(client: TestClient) -> None:
    response = _upload(client, uuid.uuid4())
    assert response.status_code == 404


def test_get_video_returns_analyzing_before_analysis_completes(
    client: TestClient, cleanup_project_ids: list, cleanup_video_ids: list
) -> None:
    project_id = _create_project(client)
    cleanup_project_ids.append(project_id)

    uploaded = _upload(client, project_id).json()
    cleanup_video_ids.append(uuid.UUID(uploaded["video_id"]))

    response = client.get(f"/videos/{uploaded['video_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == str(project_id)
    assert body["original_filename"] == "clip.mp4"
    assert body["status"] in ("analyzing", "analyzed", "failed")
    # Whichever it is depends on whether a video_analysis worker happens to
    # be running against this broker right now — this test only proves the
    # lookup wiring, not the worker itself (see test_video_analysis_worker.py
    # for that, and the crash-recovery test pattern for a real worker run).


def test_get_unknown_video_is_404(client: TestClient) -> None:
    response = client.get(f"/videos/{uuid.uuid4()}")
    assert response.status_code == 404


def test_list_videos_returns_uploaded_videos(
    client: TestClient, cleanup_project_ids: list, cleanup_video_ids: list
) -> None:
    project_id = _create_project(client)
    cleanup_project_ids.append(project_id)

    first = _upload(client, project_id, filename="first.mp4").json()
    second = _upload(client, project_id, filename="second.mp4").json()
    cleanup_video_ids.append(uuid.UUID(first["video_id"]))
    cleanup_video_ids.append(uuid.UUID(second["video_id"]))

    response = client.get(f"/projects/{project_id}/videos", headers=as_user(_OWNERS[project_id]))
    assert response.status_code == 200
    filenames = [v["original_filename"] for v in response.json()["videos"]]
    assert filenames == ["first.mp4", "second.mp4"]


def test_list_videos_for_unknown_project_is_404(client: TestClient) -> None:
    response = client.get(f"/projects/{uuid.uuid4()}/videos", headers=as_user(uuid.uuid4()))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_upload_creates_job1_event_published_end_to_end(
    client: TestClient, cleanup_project_ids: list, cleanup_video_ids: list, database_url: str
) -> None:
    """The full chain: POST /projects/{id}/videos -> videos row + Job #1 ->
    outbox row -> publisher -> Kafka, mirroring test_jobs_api.py's
    equivalent for /jobs."""
    project_id = _create_project(client)
    cleanup_project_ids.append(project_id)

    uploaded = _upload(client, project_id).json()
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
