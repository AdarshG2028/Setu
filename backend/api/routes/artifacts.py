"""Artifact download — the first endpoint in this API that serves bytes.

Until Phase 5 nothing needed one: `dummy` produced no files, and video
analysis only ever read them. Storage URIs are opaque `local://...`
strings that no client can dereference, so without this route a finished
job's output is unreachable except by looking in ./data/storage by hand.

Flat rather than nested under /jobs/{id}: an artifact is addressed by its
storage URI, which is already globally unique, and the same object can be
referenced by several stages once assets are forwarded down a chain
(media.forward_assets) -- so there is no single owning job to nest under.
"""

import mimetypes
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from backend.storage import StorageObjectNotFoundError, get_storage

router = APIRouter(tags=["artifacts"])

_CHUNK_SIZE = 64 * 1024
_FALLBACK_CONTENT_TYPE = "application/octet-stream"


@router.get("/artifacts")
def download_artifact(
    uri: Annotated[
        str,
        Query(
            # Spelled out because the natural mistake is to paste a job id
            # here: this takes a storage URI, which only ever comes from a
            # download_url in GET /jobs/{id}/artifacts.
            description=(
                "Opaque storage URI, taken from an artifact's `download_url` in "
                "`GET /jobs/{job_id}/artifacts`. Not a job id."
            ),
            examples=["local://c4d46b7c4b2f4dbbaa6428fe06737f7f.mp4"],
        ),
    ],
) -> StreamingResponse:
    """Stream a stored artifact.

    The URI arrives whole, exactly as the listing handed it out — this
    route never parses or reconstructs one, per backend/storage/base.py's
    opaque-URI contract.

    Path traversal is handled by the backend rather than re-checked here:
    LocalDiskStorage._path_for rejects any key containing a separator or
    a relative segment before it ever touches the filesystem, so a hostile
    `uri` fails the same way an unknown one does. Duplicating that check
    here would risk the two drifting apart.
    """
    try:
        stream = get_storage().open_stream(uri)
    except StorageObjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="artifact not found"
        ) from exc

    filename = Path(uri).name or "artifact"
    content_type = mimetypes.guess_type(filename)[0] or _FALLBACK_CONTENT_TYPE

    def _chunks():
        # Closing is tied to the generator rather than the request: the
        # response body is produced lazily after this function returns, so
        # a `with` block here would close the file before a single byte
        # was sent.
        try:
            while chunk := stream.read(_CHUNK_SIZE):
                yield chunk
        finally:
            stream.close()

    return StreamingResponse(
        _chunks(),
        media_type=content_type,
        # inline, not attachment: Phase 7 wants these playable in a video
        # element, and a browser download still works from the same URL.
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
