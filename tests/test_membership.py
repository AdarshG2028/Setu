"""Identity and membership (Phase 8, steps 1-3).

Before this, five of the seven /projects endpoints — including
confirm-proposal, which spends real compute — took no caller identity at
all, and the two that did took it from the request body where no
dependency could ever read it.

These tests are mostly about *rejection*. A guard that lets members
through is easy; one that actually keeps strangers out is the only part
worth having.
"""

import uuid

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.models import Project
from tests.conftest import as_user

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


def _create_project(client: TestClient, owner: uuid.UUID) -> dict:
    response = client.post("/projects", json={"name": "room"}, headers=as_user(owner))
    assert response.status_code == 201
    return response.json()


# --- the identity channel --------------------------------------------------


def test_a_request_without_identity_is_rejected(client: TestClient) -> None:
    """Every room endpoint now requires the header. Previously most of them
    accepted anonymous calls."""
    assert client.post("/projects", json={"name": "x"}).status_code == 422


def test_a_malformed_identity_is_rejected(client: TestClient) -> None:
    """Not coerced: a non-UUID id would still work as a dict key and would
    silently create a parallel identity matching no stored row."""
    response = client.post("/projects", json={"name": "x"}, headers={"X-User-Id": "alice"})

    assert response.status_code == 422
    assert "UUID" in response.text


def test_the_owner_comes_from_the_header_not_the_body(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """owner_id used to be a body field, so a client could create a project
    owned by someone else."""
    caller = uuid.uuid4()

    project = client.post(
        "/projects",
        json={"name": "room", "owner_id": str(uuid.uuid4())},  # ignored
        headers=as_user(caller),
    ).json()
    cleanup_project_ids.append(uuid.UUID(project["id"]))

    assert project["owner_id"] == str(caller)


def test_a_message_is_attributed_to_the_caller_not_a_body_field(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """sender_id was client-supplied, so anyone could post as anyone."""
    owner = uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))

    client.post(
        f"/projects/{project['id']}/messages",
        json={"content": "hello", "sender_id": str(uuid.uuid4())},  # ignored
        headers=as_user(owner),
    )

    history = client.get(
        f"/projects/{project['id']}/messages", headers=as_user(owner)
    ).json()
    assert history["messages"][0]["sender_id"] == str(owner)


# --- membership ------------------------------------------------------------


def test_the_creator_is_a_member_of_their_own_project(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """The membership row is written in the same transaction as the project.
    A project that existed but had no members would be unreachable through
    the guard — including by whoever just made it."""
    owner = uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))

    assert (
        client.get(f"/projects/{project['id']}/messages", headers=as_user(owner)).status_code
        == 200
    )


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("get", "/messages"),
        ("post", "/messages"),
        ("get", "/videos"),
        ("post", "/confirm-proposal"),
        ("post", "/preview-proposal"),
    ],
)
def test_a_stranger_is_locked_out_of_every_room_endpoint(
    client: TestClient, cleanup_project_ids: list, method: str, suffix: str
) -> None:
    owner = uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))

    call = getattr(client, method)
    kwargs = {"json": {"content": "hi"}} if method == "post" and suffix == "/messages" else {}
    response = call(
        f"/projects/{project['id']}{suffix}", headers=as_user(uuid.uuid4()), **kwargs
    )

    assert response.status_code == 404


def test_a_stranger_gets_404_not_403(client: TestClient, cleanup_project_ids: list) -> None:
    """403 would confirm the project exists, which is itself information —
    whether a given room id is real, and by extension whether someone else
    is working on it. A non-member and a non-existent project must be
    indistinguishable from outside."""
    owner = uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))

    stranger = client.get(
        f"/projects/{project['id']}/messages", headers=as_user(uuid.uuid4())
    )
    nonexistent = client.get(
        f"/projects/{uuid.uuid4()}/messages", headers=as_user(uuid.uuid4())
    )

    assert stranger.status_code == nonexistent.status_code == 404
    assert stranger.json() == nonexistent.json(), "the two must be indistinguishable"


def test_a_stranger_cannot_spend_compute(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """confirm-proposal previously took no identity at all, so anyone who
    knew a project id could submit a render job against it."""
    owner = uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))

    response = client.post(
        f"/projects/{project['id']}/confirm-proposal", headers=as_user(uuid.uuid4())
    )

    assert response.status_code == 404


def test_a_stranger_cannot_upload_into_someone_elses_room(
    client: TestClient, cleanup_project_ids: list
) -> None:
    owner = uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))

    response = client.post(
        f"/projects/{project['id']}/videos",
        files={"file": ("clip.mp4", b"bytes", "video/mp4")},
        headers=as_user(uuid.uuid4()),
    )

    assert response.status_code == 404
