"""Local-disk Storage backend — the only one V1 needs, since the API and
workers all run on the same host via uv (see README). A real deployment
would swap in an S3-compatible backend behind the same Storage interface;
nothing outside this module would change.
"""

import uuid
from pathlib import Path

from backend.storage.base import Storage, StorageObjectNotFoundError

_SCHEME = "local://"


class LocalDiskStorage(Storage):
    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, *, suggested_name: str | None = None) -> str:
        suffix = Path(suggested_name).suffix if suggested_name else ""
        key = f"{uuid.uuid4().hex}{suffix}"
        (self._base_dir / key).write_bytes(data)
        return f"{_SCHEME}{key}"

    def get(self, uri: str) -> bytes:
        path = self._path_for(uri)
        if path is None or not path.is_file():
            raise StorageObjectNotFoundError(uri)
        return path.read_bytes()

    def exists(self, uri: str) -> bool:
        path = self._path_for(uri)
        return path is not None and path.is_file()

    def _path_for(self, uri: str) -> Path | None:
        """Resolves a URI to a filesystem path, or None if it can't possibly
        be one this backend wrote — not the same as "not found", callers
        still need to check the filesystem for that.
        """
        if not uri.startswith(_SCHEME):
            return None
        key = uri.removeprefix(_SCHEME)
        # A key is always a uuid4 hex we generated in put() — reject anything
        # else outright rather than letting a garbled/malicious URI resolve
        # to a path outside base_dir.
        if "/" in key or "\\" in key or key in ("", ".", ".."):
            return None
        return self._base_dir / key
