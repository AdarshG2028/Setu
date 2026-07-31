"""Storage interface for uploaded videos and per-stage artifacts.

The URI a backend returns from put() is opaque by design: callers store it
and pass it back to get()/exists(), but never parse or construct one
themselves. That's what lets a later S3-backed implementation swap in
behind this same interface without touching anything that calls it.
"""

from abc import ABC, abstractmethod
from typing import BinaryIO


class StorageObjectNotFoundError(Exception):
    """Raised by get()/exists()/open_stream() when a URI isn't one this
    backend wrote."""


class Storage(ABC):
    @abstractmethod
    def put(self, data: bytes, *, suggested_name: str | None = None) -> str:
        """Persist data and return an opaque URI identifying it.

        suggested_name is a hint (e.g. for preserving a file extension) —
        the backend decides the actual key, so callers never assume the
        returned URI resembles the suggested name.
        """

    @abstractmethod
    def get(self, uri: str) -> bytes:
        """Return exactly what was put() under this URI.

        Raises StorageObjectNotFoundError if the URI is unknown.
        """

    @abstractmethod
    def exists(self, uri: str) -> bool:
        """Whether this URI refers to something this backend has stored."""

    @abstractmethod
    def size(self, uri: str) -> int:
        """Size in bytes.

        Needed to serve HTTP range requests: both Content-Length and the
        total in Content-Range require knowing it up front, and a video
        element cannot seek without them. An S3-backed implementation gets
        this from head_object rather than by reading the object.

        Raises StorageObjectNotFoundError if the URI is unknown.
        """

    @abstractmethod
    def open_stream(self, uri: str) -> BinaryIO:
        """Open the stored object for incremental reading.

        Separate from get() because the objects here are videos: reading a
        few hundred megabytes into memory to hand them to an HTTP response
        is fine exactly once and awful under any concurrency. Callers that
        genuinely want the whole thing in memory (a worker about to feed
        it to ffmpeg) keep using get().

        The caller owns closing the returned stream.

        Raises StorageObjectNotFoundError if the URI is unknown.
        """
