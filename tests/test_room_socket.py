"""The room WebSocket (Phase 8, step 8).

Everything here is a plain `def`, not `async def`, deliberately.
`TestClient.websocket_connect` is a synchronous context manager driven by
the session portal, and `receive_json()` blocks until some *other*
request in the same test pushes an event. Running that inside
pytest-asyncio's loop deadlocks.

The socket is pure fan-out, so every test has the same shape: open a
socket, make a REST call, assert on what arrives.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.services.room_events import RoomEventBus, get_room_events
from tests.conftest import as_user

pytestmark = pytest.mark.usefixtures("database_url")


@pytest.fixture
def room(client: TestClient, cleanup_project_ids: list):
    """A project whose owner and one joined member can both connect."""
    owner, member = uuid.uuid4(), uuid.uuid4()
    project = client.post("/projects", json={"name": "room"}, headers=as_user(owner)).json()
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    client.post(
        f"/projects/{project['id']}/members",
        json={"user_id": str(member)},
        headers=as_user(owner),
    )
    client.post(f"/projects/{project['id']}/join", headers=as_user(member))
    return project["id"], owner, member


def _url(project_id: str, user: uuid.UUID) -> str:
    # Identity in the query string: a browser cannot put a header on
    # new WebSocket(url), which is what the equivalence in deps.py exists
    # for.
    return f"/projects/{project_id}/ws?user_id={user}"


# --- the handshake ---------------------------------------------------------


def _refusal(client: TestClient, url: str) -> WebSocketDisconnect:
    """Connect expecting to be turned away, and return the close."""
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(url) as ws:
            ws.receive_json()
    return caught.value


def test_a_non_member_is_refused_the_socket(client: TestClient, room) -> None:
    """The socket carries the whole room — transcript, members, progress.
    Membership is checked before accept, so a stranger sees a failed
    handshake rather than a connection that opens and dies."""
    project_id, _, _ = room

    closed = _refusal(client, _url(project_id, uuid.uuid4()))

    assert closed.code == 1008


def test_a_socket_without_identity_is_refused(client: TestClient, room) -> None:
    project_id, _, _ = room

    closed = _refusal(client, f"/projects/{project_id}/ws")

    assert closed.code == 1008
    assert closed.reason == "identity required"


def test_a_nonexistent_room_is_refused_exactly_like_a_private_one(
    client: TestClient, room
) -> None:
    """Byte-identical close. Distinguishing them would tell a stranger
    whether a room id is real — the reason require_project_member returns
    404 rather than 403."""
    project_id, _, _ = room
    stranger = uuid.uuid4()

    private = _refusal(client, _url(project_id, stranger))
    missing = _refusal(client, _url(str(uuid.uuid4()), stranger))

    assert (private.code, private.reason) == (missing.code, missing.reason)
    assert private.reason == "project not found"


# --- fan-out ---------------------------------------------------------------


def test_a_message_reaches_a_member_who_did_not_send_it(client: TestClient, room) -> None:
    """The point of the whole phase: the other person's message appears
    without a refresh."""
    project_id, owner, member = room

    with client.websocket_connect(_url(project_id, member)) as ws:
        client.post(
            f"/projects/{project_id}/messages",
            json={"content": "hello room"},
            headers=as_user(owner),
        )

        created = ws.receive_json()

    assert created["type"] == "message.created"
    assert created["data"]["content"] == "hello room"
    assert created["data"]["sender_id"] == str(owner)


def test_the_planner_reply_is_a_second_event_not_part_of_the_first(
    client: TestClient, room
) -> None:
    """Two events from one POST, so a watching member sees the message
    land immediately rather than waiting on the planner."""
    project_id, owner, member = room

    with client.websocket_connect(_url(project_id, member)) as ws:
        client.post(
            f"/projects/{project_id}/messages",
            json={"content": "hello"},
            headers=as_user(owner),
        )

        created, replied = ws.receive_json(), ws.receive_json()

    assert [created["type"], replied["type"]] == ["message.created", "planner.replied"]
    assert created["data"]["role"] == "user"
    assert replied["data"]["role"] == "assistant"
    assert replied["data"]["sender_id"] is None


def test_every_connected_member_gets_the_same_event(client: TestClient, room) -> None:
    """Fan-out, not hand-off: one write reaches every socket in the room."""
    project_id, owner, member = room

    with client.websocket_connect(_url(project_id, owner)) as a:
        with client.websocket_connect(_url(project_id, member)) as b:
            client.post(
                f"/projects/{project_id}/messages",
                json={"content": "to both"},
                headers=as_user(owner),
            )

            first, second = a.receive_json(), b.receive_json()

    assert first == second
    assert first["data"]["content"] == "to both"


def test_joining_is_announced_to_the_room(client: TestClient, cleanup_project_ids) -> None:
    project_id_owner = uuid.uuid4()
    project = client.post(
        "/projects", json={"name": "room"}, headers=as_user(project_id_owner)
    ).json()
    cleanup_project_ids.append(uuid.UUID(project["id"]))
    invitee = uuid.uuid4()
    client.post(
        f"/projects/{project['id']}/members",
        json={"user_id": str(invitee)},
        headers=as_user(project_id_owner),
    )

    with client.websocket_connect(_url(project["id"], project_id_owner)) as ws:
        client.post(f"/projects/{project['id']}/join", headers=as_user(invitee))

        event = ws.receive_json()

    assert event["type"] == "member.joined"
    assert event["data"]["user_id"] == str(invitee)


def test_a_rooms_events_never_reach_another_room(client: TestClient, room, cleanup_project_ids) -> None:
    """The registry is keyed by project. Without that, every socket in the
    process would see every room's traffic."""
    project_id, owner, _ = room
    other_owner = uuid.uuid4()
    other = client.post("/projects", json={"name": "other"}, headers=as_user(other_owner)).json()
    cleanup_project_ids.append(uuid.UUID(other["id"]))

    with client.websocket_connect(_url(other["id"], other_owner)) as ws:
        client.post(
            f"/projects/{project_id}/messages",
            json={"content": "not yours"},
            headers=as_user(owner),
        )
        # Prove the socket is live and simply had nothing from the other
        # room: an event in its *own* room arrives, and it is the first
        # thing this socket sees.
        client.post(
            f"/projects/{other['id']}/messages",
            json={"content": "mine"},
            headers=as_user(other_owner),
        )

        event = ws.receive_json()

    assert event["data"]["content"] == "mine"


# --- seq / reconnect -------------------------------------------------------


def test_seq_advances_monotonically_within_a_room(client: TestClient, room) -> None:
    project_id, owner, member = room

    with client.websocket_connect(_url(project_id, member)) as ws:
        client.post(
            f"/projects/{project_id}/messages",
            json={"content": "one"},
            headers=as_user(owner),
        )
        client.post(
            f"/projects/{project_id}/messages",
            json={"content": "two"},
            headers=as_user(owner),
        )

        seqs = [ws.receive_json()["seq"] for _ in range(4)]

    assert seqs == sorted(seqs)
    assert len(set(seqs)) == 4


def test_the_snapshot_reports_the_same_counter_the_socket_advances(
    client: TestClient, room
) -> None:
    """This is what makes reconnect work: connect, snapshot, discard
    anything at or below snapshot.seq. If the two numbers came from
    different sources the comparison would be meaningless."""
    project_id, owner, member = room

    with client.websocket_connect(_url(project_id, member)) as ws:
        client.post(
            f"/projects/{project_id}/messages",
            json={"content": "one"},
            headers=as_user(owner),
        )
        last_seen = max(ws.receive_json()["seq"] for _ in range(2))

    snapshot = client.get(f"/projects/{project_id}", headers=as_user(owner)).json()

    assert snapshot["seq"] == last_seen


def test_seq_keeps_climbing_across_a_disconnect(client: TestClient, room) -> None:
    """A room's sequence has to outlive its subscribers. If it restarted
    when the last socket left, a reconnecting client would see numbers it
    had already used and read every reconnect as a gap."""
    project_id, owner, member = room

    with client.websocket_connect(_url(project_id, member)) as ws:
        client.post(
            f"/projects/{project_id}/messages",
            json={"content": "one"},
            headers=as_user(owner),
        )
        before = max(ws.receive_json()["seq"] for _ in range(2))

    # Nobody connected for this one; the counter must still advance.
    client.post(
        f"/projects/{project_id}/messages",
        json={"content": "two"},
        headers=as_user(owner),
    )

    with client.websocket_connect(_url(project_id, member)) as ws:
        client.post(
            f"/projects/{project_id}/messages",
            json={"content": "three"},
            headers=as_user(owner),
        )
        after = ws.receive_json()["seq"]

    assert after > before + 1, "events published with nobody listening did not advance seq"


# --- the bus itself --------------------------------------------------------


def test_publishing_to_an_empty_room_is_not_an_error() -> None:
    bus = RoomEventBus()
    assert bus.publish(uuid.uuid4(), type="message.created", data={}) == 1


def test_rooms_lists_only_rooms_with_subscribers() -> None:
    """What the progress poller works from — with nobody listening there
    is nothing to send, so polling that room would be work done for no
    observer."""
    bus = RoomEventBus()
    project_id = uuid.uuid4()
    assert bus.rooms() == []

    subscription = bus.subscribe(project_id)
    assert bus.rooms() == [project_id]

    bus.unsubscribe(subscription)
    assert bus.rooms() == []


def test_a_client_that_stops_reading_is_closed_rather_than_silently_trimmed() -> None:
    """A dropped event leaves a client quietly wrong until it happens to
    notice a seq gap. Closing sends it down the documented recovery path
    — reconnect, refetch the snapshot, resume."""
    bus = RoomEventBus(queue_size=2)
    project_id = uuid.uuid4()
    subscription = bus.subscribe(project_id)

    for _ in range(5):
        bus.publish(project_id, type="message.created", data={})

    assert subscription.overflowed is True
    drained = [subscription.queue.get_nowait() for _ in range(subscription.queue.qsize())]
    assert drained[-1] is not None and not isinstance(drained[-1], dict), (
        "the close sentinel should be the last thing a fallen-behind client gets"
    )


def test_the_bus_accessor_is_process_wide() -> None:
    """A registry request handlers write to and socket tasks read from
    only works if both reach the same object."""
    assert get_room_events() is get_room_events()
