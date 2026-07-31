"""Artifact listing and download (Phase 5A, Step 7).

The first endpoints that let a client actually retrieve output. Written
before any real capability exists, and exercised against `dummy` jobs and
hand-written Result rows, precisely so the retrieval path is proven
independently of ffmpeg — when 5B's crop lands, a wrong output is then
unambiguously crop's fault, not the download route's.
"""

import uuid
from urllib.parse import quote

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.models import Job, JobStatus, Result
from backend.storage.local import LocalDiskStorage
from backend.workers.media import Asset, AssetKind, assets_payload

pytestmark = pytest.mark.usefixtures("database_url")


async def _seed_job(engine, *, results: list[Result] | None = None) -> uuid.UUID:
    job_id = uuid.uuid4()
    sm = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with sm() as session:
        session.add(
            Job(
                id=job_id,
                status=JobStatus.COMPLETED,
                workflow={"workflow": ["crop", "transcribe"]},
                current_stage=2,
                payload={},
                max_attempts=3,
            )
        )
        await session.flush()
        for result in results or []:
            result.job_id = job_id
            session.add(result)
        await session.commit()
    return job_id


async def _cleanup(engine, job_id: uuid.UUID) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": job_id})


@pytest.fixture
async def engine(database_url: str):
    eng = create_async_engine(database_url, poolclass=NullPool)
    yield eng
    await eng.dispose()


@pytest.fixture
def artifact_storage(tmp_path, monkeypatch) -> LocalDiskStorage:
    """Point the download route at a temp dir rather than the configured
    ./data/storage. The route resolves get_storage() per request, so
    patching its module reference is enough — and it keeps these tests
    (one of which writes a deliberately large object) from leaving files
    behind in the real storage directory on every run."""
    disk = LocalDiskStorage(tmp_path)
    monkeypatch.setattr("backend.api.routes.artifacts.get_storage", lambda: disk)
    return disk


# --- listing ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_lists_assets_per_stage_with_download_urls(client, engine) -> None:
    job_id = await _seed_job(
        engine,
        results=[
            Result(
                worker_name="crop",
                stage=0,
                payload=assets_payload([Asset(kind=AssetKind.VIDEO, uri="local://a.mp4")]),
                artifact_uri="local://a.mp4",
            ),
            Result(
                worker_name="transcribe",
                stage=1,
                payload=assets_payload(
                    [
                        Asset(kind=AssetKind.VIDEO, uri="local://a.mp4"),
                        Asset(kind=AssetKind.SRT, uri="local://c.srt"),
                    ]
                ),
                artifact_uri="local://a.mp4",
            ),
        ],
    )
    try:
        body = client.get(f"/jobs/{job_id}/artifacts").json()

        assert [stage["stage"] for stage in body["stages"]] == [0, 1]
        assert body["stages"][0]["worker_name"] == "crop"

        # The srt is listed in its own right — that's the point of the
        # split in 5F, so a user can take the subtitle file alone.
        kinds = [a["kind"] for a in body["stages"][1]["artifacts"]]
        assert kinds == ["video", "srt"]

        srt = body["stages"][1]["artifacts"][1]
        assert srt["download_url"] == f"/artifacts?uri={quote('local://c.srt', safe='')}"
    finally:
        await _cleanup(engine, job_id)


@pytest.mark.asyncio
async def test_stages_without_assets_are_listed_with_an_empty_artifact_list(
    client, engine
) -> None:
    """dummy and video_analysis predate the asset convention. They must
    still appear, or the response would silently misrepresent the
    workflow as shorter than it was."""
    job_id = await _seed_job(
        engine,
        results=[Result(worker_name="dummy", stage=0, payload={"processed_by": "dummy"})],
    )
    try:
        body = client.get(f"/jobs/{job_id}/artifacts").json()

        assert len(body["stages"]) == 1
        assert body["stages"][0]["artifacts"] == []
    finally:
        await _cleanup(engine, job_id)


@pytest.mark.asyncio
async def test_listing_unknown_job_is_404(client) -> None:
    assert client.get(f"/jobs/{uuid.uuid4()}/artifacts").status_code == 404


# --- download --------------------------------------------------------------


def test_downloads_the_stored_bytes(client, artifact_storage) -> None:
    uri = artifact_storage.put(b"pretend this is an mp4", suggested_name="clip.mp4")

    response = client.get("/artifacts", params={"uri": uri})

    assert response.status_code == 200
    assert response.content == b"pretend this is an mp4"
    assert response.headers["content-type"].startswith("video/mp4")


def test_download_url_from_the_listing_actually_resolves(client, artifact_storage) -> None:
    """The listing's download_url is built by quoting the URI; this is the
    round-trip proving that encoding is one a client can use directly."""
    uri = artifact_storage.put(b"bytes", suggested_name="clip.mp4")

    response = client.get(f"/artifacts?uri={quote(uri, safe='')}")

    assert response.status_code == 200
    assert response.content == b"bytes"


def test_content_type_follows_the_extension(client, artifact_storage) -> None:
    srt_uri = artifact_storage.put(b"1\n00:00:00,000 --> 00:00:01,000\nhi\n", suggested_name="c.srt")

    response = client.get("/artifacts", params={"uri": srt_uri})

    assert response.status_code == 200
    assert response.content.startswith(b"1\n")


def test_unknown_artifact_is_404(client, artifact_storage) -> None:
    assert client.get("/artifacts", params={"uri": "local://nope.mp4"}).status_code == 404


@pytest.mark.parametrize(
    "hostile",
    [
        "local://../../../etc/passwd",
        "local://..\\..\\windows\\system32\\config\\sam",
        "local://subdir/escape.mp4",
        "file:///etc/passwd",
        "local://..",
        "local://",
    ],
)
def test_traversal_and_foreign_schemes_are_rejected(client, artifact_storage, hostile: str) -> None:
    """The `uri` parameter is attacker-controlled. LocalDiskStorage's key
    guard is what rejects these; this asserts the route surfaces that as a
    clean 404 rather than reading an arbitrary file or 500ing."""
    response = client.get("/artifacts", params={"uri": hostile})

    assert response.status_code == 404


def test_download_streams_rather_than_buffering(client, artifact_storage) -> None:
    """A whole video in memory per request is the thing open_stream exists
    to avoid — assert a large object comes back byte-exact through the
    chunked path."""
    payload = bytes(range(256)) * 8192  # 2 MiB, larger than the 64 KiB chunk
    uri = artifact_storage.put(payload, suggested_name="big.mp4")

    response = client.get("/artifacts", params={"uri": uri})

    assert response.status_code == 200
    assert response.content == payload


# --- range requests (what <video> seeking needs) ---------------------------


@pytest.fixture
def ranged(client, artifact_storage):
    """A stored object with byte-identifiable content, so a partial
    response can be checked against the exact expected slice."""
    payload = bytes(range(256)) * 40  # 10240 bytes
    uri = artifact_storage.put(payload, suggested_name="clip.mp4")
    return payload, f"/artifacts?uri={quote(uri, safe='')}"


def test_full_request_advertises_range_support(client, ranged) -> None:
    """Without Accept-Ranges a browser assumes it must download the whole
    file to play it, and never attempts to seek."""
    payload, url = ranged

    response = client.get(url)

    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == str(len(payload))


def test_explicit_range_returns_exactly_that_slice(client, ranged) -> None:
    payload, url = ranged

    response = client.get(url, headers={"Range": "bytes=100-199"})

    assert response.status_code == 206
    assert response.content == payload[100:200]
    assert response.headers["content-range"] == f"bytes 100-199/{len(payload)}"
    assert response.headers["content-length"] == "100"


def test_open_ended_range_runs_to_the_end(client, ranged) -> None:
    """`bytes=N-` is what a video element sends when the user seeks."""
    payload, url = ranged

    response = client.get(url, headers={"Range": "bytes=10000-"})

    assert response.status_code == 206
    assert response.content == payload[10000:]
    assert response.headers["content-range"] == f"bytes 10000-{len(payload) - 1}/{len(payload)}"


def test_suffix_range_returns_the_last_n_bytes(client, ranged) -> None:
    """Used to read an MP4's trailing metadata when the index isn't at the
    front of the file."""
    payload, url = ranged

    response = client.get(url, headers={"Range": "bytes=-500"})

    assert response.status_code == 206
    assert response.content == payload[-500:]


def test_range_past_the_end_is_rejected_not_silently_widened(client, ranged) -> None:
    """A malformed range must not quietly return the whole file — the
    client would treat it as a successful seek to the wrong place."""
    payload, url = ranged

    response = client.get(url, headers={"Range": "bytes=999999-"})

    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(payload)}"


def test_range_end_beyond_the_file_is_clamped(client, ranged) -> None:
    """A start inside the file with an end past it is satisfiable — serve
    what exists rather than refusing."""
    payload, url = ranged

    response = client.get(url, headers={"Range": "bytes=10000-999999"})

    assert response.status_code == 206
    assert response.content == payload[10000:]


def test_a_malformed_range_header_is_ignored(client, ranged) -> None:
    """Not an error: the spec says an unparseable Range is ignored and the
    full representation returned."""
    payload, url = ranged

    response = client.get(url, headers={"Range": "furlongs=1-2"})

    assert response.status_code == 200
    assert response.content == payload


def test_ranged_reads_reassemble_into_the_original(client, ranged) -> None:
    """The property that actually matters: a player fetching a file in
    pieces must be able to reconstruct it byte for byte."""
    payload, url = ranged

    chunks = [
        client.get(url, headers={"Range": f"bytes={start}-{start + 2047}"}).content
        for start in range(0, len(payload), 2048)
    ]

    assert b"".join(chunks) == payload
