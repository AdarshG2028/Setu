"""Object/file storage abstractions for media artifacts."""

from functools import lru_cache

from backend.core.config import get_settings
from backend.storage.base import Storage, StorageObjectNotFoundError
from backend.storage.local import LocalDiskStorage

__all__ = ["Storage", "StorageObjectNotFoundError", "get_storage"]


@lru_cache
def get_storage() -> Storage:
    settings = get_settings()
    return LocalDiskStorage(settings.storage_local_path)
