"""Identity and membership (Phase 8, steps 1-5).

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

from backend.models import Job, Project, Result
from backend.models.enums import JobStatus
from backend.workers.media import Asset, AssetKind, assets_payload
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


# --- project_jobs (step 5) -------------------------------------------------


async def _project_job_row(database_url: str, job_id: str) -> dict | None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.text(
                        "SELECT project_id, submitted_by_user_id FROM project_jobs "
                        "WHERE job_id = :j"
                    ),
                    {"j": job_id},
                )
            ).first()
        return {"project_id": str(row[0]), "submitted_by": str(row[1])} if row else None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_confirmed_job_is_bound_to_its_room_and_submitter(
    client: TestClient, cleanup_project_ids: list, database_url: str
) -> None:
    """Before this there was no path at all from a job back to a project,
    so nothing could be scoped to a room — including its artifacts."""
    owner = uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    for content in ("hi", "crop it vertically"):
        client.post(
            f"/projects/{project['id']}/messages",
            json={"content": content},
            headers=as_user(owner),
        )

    job_id = client.post(
        f"/projects/{project['id']}/confirm-proposal", headers=as_user(owner)
    ).json()["job_id"]

    row = await _project_job_row(database_url, job_id)
    assert row == {"project_id": project["id"], "submitted_by": str(owner)}


@pytest.mark.asyncio
async def test_job_ownership_records_who_actually_submitted(
    client: TestClient, cleanup_project_ids: list, database_url: str
) -> None:
    """submitted_by_user_id IS job ownership (Changelog v9) — Phase 9b
    authorizes cancellation against exactly this. A member who is not the
    owner submitting must be recorded as that job's owner, not the room's."""
    owner, member = uuid.uuid4(), uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    _invite(client, project["id"], owner, member)
    client.post(f"/projects/{project['id']}/join", headers=as_user(member))
    for content in ("hi", "crop it vertically"):
        client.post(
            f"/projects/{project['id']}/messages",
            json={"content": content},
            headers=as_user(member),
        )

    job_id = client.post(
        f"/projects/{project['id']}/confirm-proposal", headers=as_user(member)
    ).json()["job_id"]

    row = await _project_job_row(database_url, job_id)
    assert row["submitted_by"] == str(member), "recorded the room owner, not the submitter"


@pytest.mark.asyncio
async def test_a_replayed_submission_keeps_its_original_owner(
    client: TestClient, cleanup_project_ids: list, database_url: str
) -> None:
    """Setu's idempotency key returns the original job for a repeat
    submission. The mapping must not be rewritten to whoever asked second
    — that would silently transfer cancellation rights."""
    owner, member = uuid.uuid4(), uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    _invite(client, project["id"], owner, member)
    client.post(f"/projects/{project['id']}/join", headers=as_user(member))
    for content in ("hi", "crop it vertically"):
        client.post(
            f"/projects/{project['id']}/messages",
            json={"content": content},
            headers=as_user(owner),
        )

    first = client.post(
        f"/projects/{project['id']}/confirm-proposal", headers=as_user(owner)
    ).json()
    second = client.post(
        f"/projects/{project['id']}/confirm-proposal", headers=as_user(member)
    ).json()

    assert first["job_id"] == second["job_id"] and second["replayed"] is True
    row = await _project_job_row(database_url, first["job_id"])
    assert row["submitted_by"] == str(owner), "a replay reassigned job ownership"


@pytest.mark.asyncio
async def test_an_upload_binds_its_analysis_job_to_the_room(
    client: TestClient, cleanup_project_ids: list, database_url: str
) -> None:
    """Analysis jobs are jobs too — they belong in the room snapshot and
    their progress is worth broadcasting."""
    owner = uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))

    job_id = client.post(
        f"/projects/{project['id']}/videos",
        files={"file": ("clip.mp4", b"bytes", "video/mp4")},
        headers=as_user(owner),
    ).json()["job_id"]

    row = await _project_job_row(database_url, job_id)
    assert row == {"project_id": project["id"], "submitted_by": str(owner)}


# --- the room snapshot (step 6) --------------------------------------------


async def _finish_job(database_url: str, job_id: str, *, assets: list[Asset] | None) -> None:
    """Drive a job to `completed` with a final stage result.

    Workers are not running in these tests, so the terminal state has to
    be written directly. `assets=None` models a stage that produces
    nothing (video_analysis, dummy) -- precisely the case the export
    filter is supposed to drop.
    """
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                sa.update(Job)
                .where(Job.id == uuid.UUID(job_id))
                .values(status=JobStatus.COMPLETED.value, completed_at=sa.func.now())
            )
            await conn.execute(
                sa.insert(Result).values(
                    id=uuid.uuid4(),
                    job_id=uuid.UUID(job_id),
                    worker_name="render",
                    stage=0,
                    payload=assets_payload(assets) if assets else {"measured": True},
                )
            )
            await conn.commit()
    finally:
        await engine.dispose()


def _propose(client: TestClient, project_id: str, user: uuid.UUID) -> None:
    for content in ("hi", "crop it vertically"):
        client.post(
            f"/projects/{project_id}/messages",
            json={"content": content},
            headers=as_user(user),
        )


def test_a_non_member_cannot_read_the_room_snapshot(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """The snapshot is the most revealing endpoint in the phase — it
    returns the transcript, the members and the videos in one response.
    404 not 403, so a stranger cannot even confirm the room exists."""
    owner, stranger = uuid.uuid4(), uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))

    response = client.get(f"/projects/{project['id']}", headers=as_user(stranger))

    assert response.status_code == 404


def test_an_invitee_who_has_not_joined_cannot_read_the_room(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """Being invited is not being in the room; join is what grants access,
    and it would be a no-op if an invitation already showed the contents."""
    owner, invitee = uuid.uuid4(), uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    _invite(client, project["id"], owner, invitee)

    assert client.get(f"/projects/{project['id']}", headers=as_user(invitee)).status_code == 404


def test_a_fresh_room_snapshots_as_empty_rather_than_failing(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """A project has no conversation until its first message — a lazily
    created row the snapshot must treat as ordinary, not missing."""
    owner = uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))

    body = client.get(f"/projects/{project['id']}", headers=as_user(owner)).json()

    assert body["project"]["id"] == project["id"]
    assert body["messages"] == []
    assert body["videos"] == []
    assert body["active_jobs"] == []
    assert body["exports"] == []
    # The creator is already a member of their own room (steps 1-3).
    assert [m["user_id"] for m in body["members"]] == [str(owner)]


def test_the_snapshot_shows_both_members_and_the_shared_transcript(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """One request replaces five, and it is the socket's reconnect path —
    so what it returns has to match what the separate endpoints return."""
    owner, member = uuid.uuid4(), uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    _invite(client, project["id"], owner, member)
    client.post(f"/projects/{project['id']}/join", headers=as_user(member))

    client.post(
        f"/projects/{project['id']}/messages",
        json={"content": "from the owner"},
        headers=as_user(owner),
    )
    client.post(
        f"/projects/{project['id']}/messages",
        json={"content": "from the member"},
        headers=as_user(member),
    )

    body = client.get(f"/projects/{project['id']}", headers=as_user(member)).json()

    assert sorted(m["user_id"] for m in body["members"]) == sorted([str(owner), str(member)])
    user_turns = [m for m in body["messages"] if m["role"] == "user"]
    assert [m["content"] for m in user_turns] == ["from the owner", "from the member"]
    assert [m["sender_id"] for m in user_turns] == [str(owner), str(member)]


def test_a_members_upload_is_visible_and_its_job_active_to_everyone(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """The point of active_jobs in a room: the member watching progress is
    usually not the member who started the work, so they never held the
    job id that GET /jobs/{id} would need."""
    owner, member = uuid.uuid4(), uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    _invite(client, project["id"], owner, member)
    client.post(f"/projects/{project['id']}/join", headers=as_user(member))

    job_id = client.post(
        f"/projects/{project['id']}/videos",
        files={"file": ("clip.mp4", b"bytes", "video/mp4")},
        headers=as_user(member),
    ).json()["job_id"]

    body = client.get(f"/projects/{project['id']}", headers=as_user(owner)).json()

    assert [v["original_filename"] for v in body["videos"]] == ["clip.mp4"]
    assert [j["id"] for j in body["active_jobs"]] == [job_id]
    assert body["active_jobs"][0]["workflow"] == ["video_analysis"]


@pytest.mark.asyncio
async def test_a_finished_job_leaves_active_and_appears_as_an_export(
    client: TestClient, cleanup_project_ids: list, database_url: str
) -> None:
    """The export list is the room's version history (architecture doc,
    Phase 8) — derived from completed jobs rather than stored."""
    owner = uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    _propose(client, project["id"], owner)
    job_id = client.post(
        f"/projects/{project['id']}/confirm-proposal", headers=as_user(owner)
    ).json()["job_id"]

    await _finish_job(
        database_url, job_id, assets=[Asset(kind=AssetKind.VIDEO, uri="local://final.mp4")]
    )

    body = client.get(f"/projects/{project['id']}", headers=as_user(owner)).json()

    assert [j["id"] for j in body["active_jobs"]] == []
    assert len(body["exports"]) == 1
    export = body["exports"][0]
    assert export["job_id"] == job_id
    assert export["completed_at"] is not None
    # Downloadable through the same route as everywhere else, not a
    # second URL scheme invented for the room.
    assert export["artifacts"] == [
        {
            "kind": "video",
            "uri": "local://final.mp4",
            "download_url": "/artifacts?uri=local%3A%2F%2Ffinal.mp4",
        }
    ]


@pytest.mark.asyncio
async def test_a_completed_preview_is_not_an_export(
    client: TestClient, cleanup_project_ids: list, database_url: str
) -> None:
    """A preview is the same workflow at low resolution, so nothing about
    its shape distinguishes it — only the _preview payload flag. Letting
    one into the version list would offer a throwaway 480p render as if it
    were the finished piece."""
    owner = uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    _propose(client, project["id"], owner)
    job_id = client.post(
        f"/projects/{project['id']}/preview-proposal", headers=as_user(owner)
    ).json()["job_id"]

    await _finish_job(
        database_url, job_id, assets=[Asset(kind=AssetKind.VIDEO, uri="local://preview.mp4")]
    )

    body = client.get(f"/projects/{project['id']}", headers=as_user(owner)).json()

    assert body["exports"] == []


@pytest.mark.asyncio
async def test_a_completed_analysis_job_is_not_an_export(
    client: TestClient, cleanup_project_ids: list, database_url: str
) -> None:
    """Excluded because it produced no assets, not because it is named
    video_analysis — so a future non-producing capability needs no change
    to the filter."""
    owner = uuid.uuid4()
    project = _create_project(client, owner)
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    job_id = client.post(
        f"/projects/{project['id']}/videos",
        files={"file": ("clip.mp4", b"bytes", "video/mp4")},
        headers=as_user(owner),
    ).json()["job_id"]

    await _finish_job(database_url, job_id, assets=None)

    body = client.get(f"/projects/{project['id']}", headers=as_user(owner)).json()

    assert body["exports"] == []
    assert body["active_jobs"] == []


def test_the_snapshot_never_leaks_another_rooms_jobs(
    client: TestClient, cleanup_project_ids: list
) -> None:
    """project_jobs is what scopes a job to a room; without that join this
    would happily list every job in the system."""
    owner, other_owner = uuid.uuid4(), uuid.uuid4()
    mine = _create_project(client, owner)
    theirs = _create_project(client, other_owner)
    cleanup_project_ids.extend([uuid.UUID(mine["id"]), uuid.UUID(theirs["id"])])

    client.post(
        f"/projects/{theirs['id']}/videos",
        files={"file": ("other.mp4", b"bytes", "video/mp4")},
        headers=as_user(other_owner),
    )

    body = client.get(f"/projects/{mine['id']}", headers=as_user(owner)).json()

    assert body["active_jobs"] == []
    assert body["videos"] == []
