"""Artifact download — the first endpoint in this API that serves bytes.

Until Phase 5 nothing needed one: `dummy` produced no files, and video
analysis only ever read them. Storage URIs are opaque `local://...`
strings that no client can dereference, so without this route a finished
job's output is unreachable except by looking in ./data/storage by hand.

Flat rather than nested under /jobs/{id}: an artifact is addressed by its
storage URI, which is already globally unique, and the same object can be
referenced by several stages once assets are forwarded down a chain
(media.forward_assets) -- so there is no single owning job to nest under.

**Range requests are supported**, which for a video product is not a nicety:
without them a <video> element can play a file but cannot seek, so
scrubbing through a render to check the crop framing or caption timing --
the entire point of reviewing output -- silently does nothing.
"""

import mimetypes
import re
from pathlib import Path
from typing import BinaryIO

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from backend.api.deps import ArtifactAccessDep
from backend.storage import StorageObjectNotFoundError, get_storage

router = APIRouter(tags=["artifacts"])

_CHUNK_SIZE = 64 * 1024
_FALLBACK_CONTENT_TYPE = "application/octet-stream"

# Only the single-range form. Browsers request one range at a time for
# media playback; multipart/byteranges exists but nothing that matters
# here emits it, and supporting it would mean generating MIME parts for
# no practical gain.
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Resolve a Range header to inclusive (start, end) byte offsets.

    Returns None when there is no range to honour, and raises 416 when one
    was asked for but cannot be satisfied -- the distinction matters,
    since a malformed range must not silently return the whole file.

    Handles all three forms: `bytes=500-999` (explicit), `bytes=500-`
    (open-ended, what a video element sends when it seeks), and
    `bytes=-500` (the final N bytes, used to read an MP4's trailing
    metadata when the index is not at the front).
    """
    if not header:
        return None

    match = _RANGE.match(header.strip())
    if not match or size <= 0:
        return None

    raw_start, raw_end = match.group(1), match.group(2)
    if not raw_start and not raw_end:
        return None

    if not raw_start:  # bytes=-N -> the last N bytes
        length = int(raw_end)
        if length <= 0:
            raise HTTPException(
                status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                headers={"Content-Range": f"bytes */{size}"},
                detail="unsatisfiable range",
            )
        start, end = max(0, size - length), size - 1
    else:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1
        end = min(end, size - 1)

    if start > end or start >= size:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{size}"},
            detail="unsatisfiable range",
        )
    return start, end


def _stream(handle: BinaryIO, start: int, length: int):
    """Yield `length` bytes from `start`, in chunks.

    Closing is tied to the generator rather than the request: the response
    body is produced lazily after the handler returns, so closing in the
    handler would shut the file before a single byte was sent.
    """
    try:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        handle.close()


@router.get("/artifacts")
async def download_artifact(request: Request, uri: ArtifactAccessDep) -> StreamingResponse:
    """Stream a stored artifact, honouring Range requests.

    Both the `uri` and the `job_id` it is authorized against are declared
    by require_artifact_access (backend/api/deps.py), which returns the
    URI once the caller is confirmed to be a member of the room that job
    belongs to. Phase 8: before this, anyone who could name a URI could
    fetch its bytes.

    The URI arrives whole, exactly as the listing handed it out — this
    route never parses or reconstructs one, per backend/storage/base.py's
    opaque-URI contract.

    Path traversal is handled by the backend rather than re-checked here:
    LocalDiskStorage._path_for rejects any key containing a separator or
    a relative segment before it ever touches the filesystem, so a hostile
    `uri` fails the same way an unknown one does. Duplicating that check
    here would risk the two drifting apart. The guard runs first, so
    reaching that code now also requires a hostile URI to have been
    recorded as a real asset of a room's job.
    """
    storage = get_storage()
    try:
        size = storage.size(uri)
        handle = storage.open_stream(uri)
    except StorageObjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="artifact not found"
        ) from exc

    try:
        window = _parse_range(request.headers.get("range"), size)
    except HTTPException:
        handle.close()
        raise

    filename = Path(uri).name or "artifact"
    headers = {
        # Advertised so a client knows seeking is available at all; without
        # it browsers assume the whole file must be downloaded to play.
        "Accept-Ranges": "bytes",
        # inline, not attachment: Phase 7 wants these playable in a video
        # element, and a browser download still works from the same URL.
        "Content-Disposition": f'inline; filename="{filename}"',
    }
    content_type = mimetypes.guess_type(filename)[0] or _FALLBACK_CONTENT_TYPE

    if window is None:
        headers["Content-Length"] = str(size)
        return StreamingResponse(
            _stream(handle, 0, size), media_type=content_type, headers=headers
        )

    start, end = window
    length = end - start + 1
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(length)
    return StreamingResponse(
        _stream(handle, start, length),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=content_type,
        headers=headers,
    )
