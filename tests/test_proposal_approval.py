"""Approval collection in a real multi-member room (Phase 9a, step 2).

The policy arithmetic itself is proven exhaustively and without a database
in test_approval_policy.py. What is tested here is everything around it:
that votes are collected from the right people, that exactly one
submission can happen, and that a decision reaches the room.
"""

import uuid

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import as_user

pytestmark = pytest.mark.usefixtures("database_url")


def _room(client: TestClient, cleanup_project_ids: list, members: int = 2):
    """A room with `members` active members and one pending proposal."""
    owner = uuid.uuid4()
    project = client.post("/projects", json={"name": "vote"}, headers=as_user(owner)).json()
    cleanup_project_ids.append(uuid.UUID(project["id"]))

    joined = []
    for _ in range(members - 1):
        member = uuid.uuid4()
        client.post(
            f"/projects/{project['id']}/members",
            json={"user_id": str(member)},
            headers=as_user(owner),
        )
        client.post(f"/projects/{project['id']}/join", headers=as_user(member))
        joined.append(member)

    for content in ("hi", "crop it vertically"):
        client.post(
            f"/projects/{project['id']}/messages",
            json={"content": content},
            headers=as_user(owner),
        )
    proposal_id = client.get(
        f"/projects/{project['id']}/proposals", headers=as_user(owner)
    ).json()["proposals"][0]["id"]
    return project["id"], owner, joined, proposal_id


async def _set_policy(database_url: str, project_id: str, policy: str) -> None:
    """No endpoint sets the policy yet — the roadmap does not ask for one,
    and `team` is the default every room gets."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                sa.text("UPDATE projects SET approval_policy = :p WHERE id = :i"),
                {"p": policy, "i": uuid.UUID(project_id)},
            )
            await conn.commit()
    finally:
        await engine.dispose()


# --- team (the default) ----------------------------------------------------


def test_a_partial_team_approval_runs_nothing(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """The property that matters most: real compute must not start until
    the room's policy is actually satisfied."""
    _, owner, _, proposal_id = _room(client, cleanup_project_ids, members=3)

    body = client.post(f"/proposals/{proposal_id}/approve", headers=as_user(owner)).json()

    assert body["job_id"] is None
    assert body["proposal"]["status"] == "pending"
    assert len(body["proposal"]["approval"]["awaiting"]) == 2


def test_the_last_approval_submits_exactly_one_job(
    client: TestClient, cleanup_project_ids: list
) -> None:
    project_id, owner, members, proposal_id = _room(client, cleanup_project_ids, members=3)

    client.post(f"/proposals/{proposal_id}/approve", headers=as_user(owner))
    client.post(f"/proposals/{proposal_id}/approve", headers=as_user(members[0]))
    last = client.post(
        f"/proposals/{proposal_id}/approve", headers=as_user(members[1])
    ).json()

    assert last["job_id"] is not None
    assert last["proposal"]["status"] == "submitted"
    # One job in the room, not three.
    snapshot = client.get(f"/projects/{project_id}", headers=as_user(owner)).json()
    job_ids = {j["id"] for j in snapshot["active_jobs"]} | {
        e["job_id"] for e in snapshot["exports"]
    } | {j["id"] for j in snapshot["ended_jobs"]}
    assert job_ids == {last["job_id"]}


def test_one_reject_ends_a_team_proposal_without_waiting(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """Unanimity is already impossible, so the room is handed back its
    conversation rather than made to finish voting on a dead proposal."""
    _, owner, members, proposal_id = _room(client, cleanup_project_ids, members=3)

    client.post(f"/proposals/{proposal_id}/approve", headers=as_user(owner))
    body = client.post(
        f"/proposals/{proposal_id}/reject", headers=as_user(members[0])
    ).json()

    assert body["proposal"]["status"] == "rejected"
    assert body["job_id"] is None
    # members[1] never voted, and never needs to.
    assert body["proposal"]["approval"]["awaiting"] == []


def test_a_member_may_change_their_mind_while_it_is_open(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """One vote row per member, upserted. A tally would have to be
    un-counted when somebody switched."""
    _, owner, members, proposal_id = _room(client, cleanup_project_ids, members=3)

    client.post(f"/proposals/{proposal_id}/approve", headers=as_user(members[0]))
    body = client.post(
        f"/proposals/{proposal_id}/approve", headers=as_user(members[0])
    ).json()

    assert body["proposal"]["approval"]["approved_by"] == [str(members[0])]
    assert body["proposal"]["status"] == "pending"


def test_switching_to_reject_ends_it(client: TestClient, cleanup_project_ids: list) -> None:
    _, owner, members, proposal_id = _room(client, cleanup_project_ids, members=3)

    client.post(f"/proposals/{proposal_id}/approve", headers=as_user(members[0]))
    body = client.post(
        f"/proposals/{proposal_id}/reject", headers=as_user(members[0])
    ).json()

    assert body["proposal"]["status"] == "rejected"


def test_an_outstanding_invitation_does_not_block_the_room(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """Counting invitees would make unanimity unreachable the moment
    anyone was invited — the room could never execute anything until every
    invitee got round to accepting."""
    project_id, owner, _, proposal_id = _room(client, cleanup_project_ids, members=1)
    client.post(
        f"/projects/{project_id}/members",
        json={"user_id": str(uuid.uuid4())},  # invited, never joins
        headers=as_user(owner),
    )

    body = client.post(f"/proposals/{proposal_id}/approve", headers=as_user(owner)).json()

    assert body["proposal"]["status"] == "submitted"


def test_a_stranger_cannot_vote(client: TestClient, cleanup_project_ids: list) -> None:
    _, _, _, proposal_id = _room(client, cleanup_project_ids, members=2)

    response = client.post(f"/proposals/{proposal_id}/approve", headers=as_user(uuid.uuid4()))

    assert response.status_code == 404


# --- admin ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_under_admin_only_the_owners_vote_counts(
    client: TestClient, cleanup_project_ids: list, database_url: str
) -> None:
    """Other members' votes are advisory. A room could unanimously approve
    and still be waiting on the one person the policy names."""
    project_id, owner, members, proposal_id = _room(client, cleanup_project_ids, members=3)
    await _set_policy(database_url, project_id, "admin")

    client.post(f"/proposals/{proposal_id}/approve", headers=as_user(members[0]))
    body = client.post(
        f"/proposals/{proposal_id}/approve", headers=as_user(members[1])
    ).json()

    assert body["proposal"]["status"] == "pending"
    assert body["proposal"]["approval"]["awaiting"] == [str(owner)]
    assert body["proposal"]["approval"]["required"] == 1


@pytest.mark.asyncio
async def test_under_admin_the_owner_alone_submits(
    client: TestClient, cleanup_project_ids: list, database_url: str
) -> None:
    project_id, owner, _, proposal_id = _room(client, cleanup_project_ids, members=3)
    await _set_policy(database_url, project_id, "admin")

    body = client.post(f"/proposals/{proposal_id}/approve", headers=as_user(owner)).json()

    assert body["proposal"]["status"] == "submitted"
    assert body["job_id"] is not None


@pytest.mark.asyncio
async def test_under_admin_a_members_rejection_does_not_kill_it(
    client: TestClient, cleanup_project_ids: list, database_url: str
) -> None:
    project_id, owner, members, proposal_id = _room(client, cleanup_project_ids, members=3)
    await _set_policy(database_url, project_id, "admin")

    body = client.post(
        f"/proposals/{proposal_id}/reject", headers=as_user(members[0])
    ).json()

    assert body["proposal"]["status"] == "pending"


# --- the room hears about it ------------------------------------------------


def test_a_proposal_is_announced_to_the_room(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """proposal.created rides the same socket as everything else, so a
    member watching sees the proposal card appear without refreshing."""
    owner = uuid.uuid4()
    project = client.post("/projects", json={"name": "sock"}, headers=as_user(owner)).json()
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    client.post(
        f"/projects/{project['id']}/messages",
        json={"content": "hi"},
        headers=as_user(owner),
    )

    url = f"/projects/{project['id']}/ws?user_id={owner}"
    with client.websocket_connect(url) as ws:
        client.post(
            f"/projects/{project['id']}/messages",
            json={"content": "crop it vertically"},
            headers=as_user(owner),
        )
        events = [ws.receive_json() for _ in range(3)]

    assert [e["type"] for e in events] == [
        "message.created",
        "planner.replied",
        "proposal.created",
    ]
    assert events[-1]["data"]["status"] == "pending"


def test_a_decision_is_announced_to_the_room(
    client: TestClient, cleanup_project_ids: list
) -> None:
    project_id, owner, members, proposal_id = _room(client, cleanup_project_ids, members=2)

    url = f"/projects/{project_id}/ws?user_id={members[0]}"
    with client.websocket_connect(url) as ws:
        client.post(f"/proposals/{proposal_id}/approve", headers=as_user(owner))
        event = ws.receive_json()

    assert event["type"] == "proposal.updated"
    assert event["data"]["id"] == proposal_id
