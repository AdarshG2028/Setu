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


# --- invite / join / list (step 4) -----------------------------------------


def _invite(client: TestClient, project_id: str, owner: uuid.UUID, invitee: uuid.UUID):
    return client.post(
        f"/projects/{project_id}/members",
        json={"user_id": str(invitee)},
        headers=as_user(owner),
    )


def test_an_invitation_alone_does_not_grant_access(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """The distinction the whole invite/join split exists for: being added
    to a room without having asked is not the same as being in it. If an
    invitation granted access immediately, join would be a no-op."""
    owner, invitee = uuid.uuid4(), uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))

    assert _invite(client, project["id"], owner, invitee).status_code == 201

    assert (
        client.get(f"/projects/{project['id']}/messages", headers=as_user(invitee)).status_code
        == 404
    )


def test_joining_turns_an_invitation_into_access(
    client: TestClient, cleanup_project_ids: list
) -> None:
    owner, invitee = uuid.uuid4(), uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    _invite(client, project["id"], owner, invitee)

    joined = client.post(f"/projects/{project['id']}/join", headers=as_user(invitee))

    assert joined.status_code == 200
    assert joined.json()["role"] == "member"
    assert (
        client.get(f"/projects/{project['id']}/messages", headers=as_user(invitee)).status_code
        == 200
    )


def test_join_without_an_invitation_is_refused(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """The security property that makes /join safe to expose without the
    membership guard: knowing a project id must not be enough to enter."""
    owner, stranger = uuid.uuid4(), uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))

    response = client.post(f"/projects/{project['id']}/join", headers=as_user(stranger))

    assert response.status_code == 404
    assert (
        client.get(f"/projects/{project['id']}/messages", headers=as_user(stranger)).status_code
        == 404
    )


def test_join_on_a_nonexistent_project_looks_identical(client: TestClient) -> None:
    """Same reasoning as the guard's 404: 'that room exists but you weren't
    invited' is itself information."""
    uninvited = client.post(f"/projects/{uuid.uuid4()}/join", headers=as_user(uuid.uuid4()))

    assert uninvited.status_code == 404


def test_only_the_owner_can_invite(client: TestClient, cleanup_project_ids: list) -> None:
    """A member who could invite could hand the room to anyone, which makes
    the owner's control over who is present meaningless."""
    owner, member, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    _invite(client, project["id"], owner, member)
    client.post(f"/projects/{project['id']}/join", headers=as_user(member))

    response = _invite(client, project["id"], member, outsider)

    assert response.status_code == 403


def test_a_stranger_inviting_gets_404_not_403(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """A member who isn't the owner already knows the room exists, so 403 is
    the useful answer. A stranger must still learn nothing."""
    owner = uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))

    response = _invite(client, project["id"], uuid.uuid4(), uuid.uuid4())

    assert response.status_code == 404


def test_members_list_shows_arrivals_and_outstanding_invitations(
    client: TestClient, cleanup_project_ids: list
) -> None:
    owner, joined_user, pending = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    _invite(client, project["id"], owner, joined_user)
    client.post(f"/projects/{project['id']}/join", headers=as_user(joined_user))
    _invite(client, project["id"], owner, pending)

    body = client.get(f"/projects/{project['id']}/members", headers=as_user(owner)).json()

    roles = {m["user_id"]: m["role"] for m in body["members"]}
    assert roles[str(owner)] == "owner"
    assert roles[str(joined_user)] == "member"
    assert roles[str(pending)] == "invited", "a member should see who has been asked"


def test_inviting_twice_is_harmless(client: TestClient, cleanup_project_ids: list) -> None:
    """Clients retry. A second invitation must not error, and must not
    demote someone who has already joined back to invited."""
    owner, invitee = uuid.uuid4(), uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    _invite(client, project["id"], owner, invitee)
    client.post(f"/projects/{project['id']}/join", headers=as_user(invitee))

    assert _invite(client, project["id"], owner, invitee).status_code == 201

    body = client.get(f"/projects/{project['id']}/members", headers=as_user(owner)).json()
    roles = {m["user_id"]: m["role"] for m in body["members"]}
    assert roles[str(invitee)] == "member", "re-inviting demoted a member back to invited"


def test_a_second_member_sees_the_same_conversation(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """The point of the whole phase: one shared room, not two private ones."""
    owner, invitee = uuid.uuid4(), uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    _invite(client, project["id"], owner, invitee)
    client.post(f"/projects/{project['id']}/join", headers=as_user(invitee))

    client.post(
        f"/projects/{project['id']}/messages",
        json={"content": "from the owner"},
        headers=as_user(owner),
    )
    client.post(
        f"/projects/{project['id']}/messages",
        json={"content": "from the invitee"},
        headers=as_user(invitee),
    )

    for viewer in (owner, invitee):
        history = client.get(
            f"/projects/{project['id']}/messages", headers=as_user(viewer)
        ).json()["messages"]
        user_turns = [m for m in history if m["role"] == "user"]
        assert [m["content"] for m in user_turns] == ["from the owner", "from the invitee"]
        assert [m["sender_id"] for m in user_turns] == [str(owner), str(invitee)]
