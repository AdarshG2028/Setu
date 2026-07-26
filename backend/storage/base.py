"""Storage interface for uploaded videos and per-stage artifacts.

The URI a backend returns from put() is opaque by design: callers store it
and pass it back to get()/exists(), but never parse or construct one
themselves. That's what lets a later S3-backed implementation swap in
behind this same interface without touching anything that calls it.
"""

from abc import ABC, abstractmethod


class StorageObjectNotFoundError(Exception):
    """Raised by get()/exists() when a URI isn't one this backend wrote."""


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
