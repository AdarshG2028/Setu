"""POST /projects/{id}/confirm-proposal: the explicit confirmation entry
point (Changelog v8, Phase 4). No free-text chat confirmation -- a proposal
is only ever confirmed by calling this endpoint. Runs against StaticPlanner
(the app's default), which produces a real `{"type": "proposal"}` message
on the second user turn, same as test_conversations_api.py.
"""

import uuid

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.models import Project

pytestmark = pytest.mark.usefixtures("database_url")


@pytest.fixture
async def cleanup_project_ids(database_url: str):
    created: list[uuid.UUID] = []
    yield created
    if not created:
        return
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.connect() as conn:
        for project_id in created:
            await conn.execute(sa.delete(Project).where(Project.id == project_id))
        await conn.commit()
    await engine.dispose()


def _create_project(client: TestClient) -> dict:
    response = client.post("/projects", json={"owner_id": str(uuid.uuid4())})
    assert response.status_code == 201
    return response.json()


def _post_message(client: TestClient, project_id: str, sender_id: str, content: str) -> dict:
    response = client.post(
        f"/projects/{project_id}/messages",
        json={"sender_id": sender_id, "content": content},
    )
    assert response.status_code == 200
    return response.json()


def test_confirm_proposal_with_no_proposal_yet_is_409(
    client: TestClient, cleanup_project_ids: list
) -> None:
    project = _create_project(client)
    cleanup_project_ids.append(uuid.UUID(project["id"]))

    _post_message(client, project["id"], str(uuid.uuid4()), "hi")  # clarifying turn only

    response = client.post(f"/projects/{project['id']}/confirm-proposal")
    assert response.status_code == 409


def test_confirm_proposal_for_unknown_project_is_404(client: TestClient) -> None:
    response = client.post(f"/projects/{uuid.uuid4()}/confirm-proposal")
    assert response.status_code == 404


def test_confirm_proposal_submits_a_job(client: TestClient, cleanup_project_ids: list) -> None:
    project = _create_project(client)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    sender_id = str(uuid.uuid4())

    _post_message(client, project["id"], sender_id, "hi")
    _post_message(client, project["id"], sender_id, "crop it vertically")

    response = client.post(f"/projects/{project['id']}/confirm-proposal")
    assert response.status_code == 200
    body = response.json()
    uuid.UUID(body["job_id"])  # doesn't raise
    assert body["replayed"] is False


def test_confirm_proposal_is_idempotent(client: TestClient, cleanup_project_ids: list) -> None:
    project = _create_project(client)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    sender_id = str(uuid.uuid4())

    _post_message(client, project["id"], sender_id, "hi")
    _post_message(client, project["id"], sender_id, "crop it vertically")

    first = client.post(f"/projects/{project['id']}/confirm-proposal").json()
    second = client.post(f"/projects/{project['id']}/confirm-proposal").json()

    assert first["job_id"] == second["job_id"]
    assert first["replayed"] is False
    assert second["replayed"] is True


# --- POST /projects/{id}/preview-proposal (Phase 5A, Step 8) ---------------


def test_preview_proposal_submits_a_job(client: TestClient, cleanup_project_ids: list) -> None:
    project = _create_project(client)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    sender_id = str(uuid.uuid4())

    _post_message(client, project["id"], sender_id, "hi")
    _post_message(client, project["id"], sender_id, "crop it vertically")

    response = client.post(f"/projects/{project['id']}/preview-proposal")

    assert response.status_code == 200
    body = response.json()
    uuid.UUID(body["job_id"])
    assert body["replayed"] is False


def test_preview_and_confirm_produce_different_jobs(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """The reason the idempotency keys are namespaced. Everything else about
    the two calls is identical by construction, so a shared key would make
    confirm replay the preview's low-resolution job and hand the user a
    480p file as their final render."""
    project = _create_project(client)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    sender_id = str(uuid.uuid4())

    _post_message(client, project["id"], sender_id, "hi")
    _post_message(client, project["id"], sender_id, "crop it vertically")

    preview = client.post(f"/projects/{project['id']}/preview-proposal").json()
    confirm = client.post(f"/projects/{project['id']}/confirm-proposal").json()

    assert preview["job_id"] != confirm["job_id"]
    assert confirm["replayed"] is False


def test_preview_proposal_is_idempotent(client: TestClient, cleanup_project_ids: list) -> None:
    """Re-previewing the same proposal replays rather than re-rendering —
    the point of a preview is that it's cheap to ask for repeatedly."""
    project = _create_project(client)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    sender_id = str(uuid.uuid4())

    _post_message(client, project["id"], sender_id, "hi")
    _post_message(client, project["id"], sender_id, "crop it vertically")

    first = client.post(f"/projects/{project['id']}/preview-proposal").json()
    second = client.post(f"/projects/{project['id']}/preview-proposal").json()

    assert first["job_id"] == second["job_id"]
    assert second["replayed"] is True


def test_preview_proposal_with_no_proposal_yet_is_409(
    client: TestClient, cleanup_project_ids: list
) -> None:
    project = _create_project(client)
    cleanup_project_ids.append(uuid.UUID(project["id"]))

    response = client.post(f"/projects/{project['id']}/preview-proposal")

    assert response.status_code == 409


def test_preview_proposal_for_unknown_project_is_404(client: TestClient) -> None:
    assert client.post(f"/projects/{uuid.uuid4()}/preview-proposal").status_code == 404
