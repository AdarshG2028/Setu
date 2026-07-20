"""Status enumerations shared across models and services.

Stored as VARCHAR with a CHECK constraint rather than native Postgres enums:
adding a status stays a plain migration instead of an ALTER TYPE.
"""

from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"
    CANCELLED = "cancelled"

    @classmethod
    def terminal(cls) -> frozenset["JobStatus"]:
        return frozenset({cls.COMPLETED, cls.DEAD_LETTERED, cls.CANCELLED})


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


class ExecutionStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
