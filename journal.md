# Setu — Project Journal

Running log of what's been built, why, and what's still open. Newest entries
at the bottom of each phase section. See `README.md` for how to run things.

---

## Phase 0 — Infrastructure

**Goal:** FastAPI skeleton, PostgreSQL schema, docker-compose with Postgres +
Kafka-compatible broker. No business logic yet.

### 2026-07-18 — `8601763` Phase 0: initialize uv project scaffold

Bootstrapped the repo with `uv init`. Declared core dependencies (`fastapi`,
`uvicorn`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `pydantic-settings`) in
`pyproject.toml`. No application code yet — just the scaffold and lockfile.

### 2026-07-20 — `6459619` Phase 0: infrastructure skeleton, schema, and local stack

Built out the `backend/` package (13 subpackages per the spec: `api`, `core`,
`database`, `models`, `repositories`, `services`, `workers`, `planner`,
`workflow`, `storage`, `messaging`, `observability`, `shared` — `planner` and
`workflow` are Phase 4/5 concerns but scaffolded now since empty packages are
free and restructuring later isn't).

Added:
- `backend/api/main.py` — FastAPI app factory (`create_app()`) with a
  lifespan stub for the DB engine / Kafka clients to attach to later, plus a
  `GET /health` liveness probe (deliberately no DB/Kafka calls — readiness
  checks come once there's something to check).
- `backend/core/config.py` — typed `Settings` via pydantic-settings, loaded
  from `.env`. Includes `database_url` as a validated `PostgresDsn`.
- `backend/database/session.py` — async engine, `async_sessionmaker`, and a
  `get_session()` FastAPI dependency. Commits are left explicit to the
  caller, since the outbox pattern (Phase 1) needs control over the
  transaction boundary.
- Five SQLAlchemy models, one file each, all re-exported from
  `backend/models/__init__.py` (required for Alembic autogenerate to see
  them):
  - **`Job`** — `workflow` JSONB holds the ordered stage list
    (`{"workflow": [...]}`)  and `current_stage` tracks position. The engine
    (Phase 4) iterates this data; no chain is hardcoded in Python.
  - **`Result`** — `UNIQUE(job_id, stage)`. This is what makes worker
    redelivery idempotent: a retried stage overwrites its row instead of
    duplicating it.
  - **`OutboxEvent`** — has a partial index `WHERE published_at IS NULL` so
    the publisher's poll query stays cheap as published rows accumulate.
    `partition_key` keeps per-job Kafka ordering.
  - **`IdempotencyKey`** — the client-supplied key is the primary key, so a
    concurrent duplicate submission loses the insert race rather than
    creating two jobs. `request_hash` catches same-key-different-body
    client bugs.
  - **`WorkerExecution`** — `UNIQUE(job_id, stage, attempt)`. This is the
    audit trail the crash-recovery demo reads: killing a worker mid-job
    leaves a `started` row with no terminal status, and the retry shows up
    as a new attempt, not a duplicate.
  - Status columns are `VARCHAR` + `CHECK` constraints, not native Postgres
    enums — adding a status later stays an ordinary migration instead of
    `ALTER TYPE`.
- Alembic initialized at `backend/database/migrations/`, wired so the DB URL
  comes from `backend.core.config` (single source of truth, not
  `alembic.ini`).
- `0001_initial_schema.py` — hand-written, since Docker wasn't running at
  the time and there was no live Postgres to autogenerate against.
- `tests/test_migration_matches_models.py` — diffs Alembic's offline DDL
  output against DDL generated straight from `Base.metadata`, to catch drift
  in a hand-written migration. Mutation-tested during development (removed a
  column and an index from the migration, confirmed the guard failed with
  the right message, restored) so it's known to actually catch drift, not
  just pass trivially.
- Reconciled the scaffold: deleted the placeholder root `main.py`, added
  hatchling build config so `backend.*` imports resolve from an installed
  `setu` package, entry point is `uv run uvicorn backend.api.main:app`.

**Note on process:** this session initially believed Phase 0 was only ~20%
done, having checked master's git log alone. A prior session had actually
built most of Phase 0 (FastAPI skeleton, all five models, an initial
migration, and a working `docker-compose.yml`) on an unmerged worktree branch
(`worktree-phase-0-infrastructure`) that wasn't visible without `git branch
-a` / `git worktree list`. Reconciled by keeping this session's version
(broader package tree, migration-drift test) and porting over the other
branch's `docker-compose.yml`, which also surfaced a real bug: this
session's Kafka default was `9092`, but the compose file's external listener
is `19092`. Branch and worktree have since been deleted after their useful
part (the compose file) was merged in.

### 2026-07-20 — `e92550c` Phase 0: verify local stack, add schema integration tests and README

Brought the actual stack up (`docker compose -f docker/docker-compose.yml up
-d` — Postgres 16, Redpanda as the Kafka-protocol broker, Redpanda Console)
and validated against it rather than trusting the offline DDL comparison
alone:

- `uv run alembic upgrade head` against real Postgres — first attempt failed
  (`Can't locate revision '0001_initial_schema'`) because the Postgres
  volume had survived from the earlier worktree session with that branch's
  different revision ID applied. Confirmed all five tables were empty, then
  reset the volume and re-applied cleanly.
- `uv run alembic check` against the live DB → **no drift detected**. This
  is the real confirmation that the hand-written `0001` migration exactly
  matches the ORM models — the offline DDL-diff test could only approximate
  this.
- `alembic downgrade base` → `upgrade head` round-trip confirmed reversible
  (6 tables → 1 → 6).
- Verified the partial index on `outbox_events` landed with its `WHERE
  (published_at IS NULL)` clause via `pg_indexes`, and confirmed with
  `EXPLAIN` that Postgres's planner actually chooses it over a seq scan.
- Added `aiokafka` and round-tripped a produce/consume against
  `localhost:19092` — confirmed the external listener port fix from the
  compose reconciliation actually works, not just compiles.
- `tests/conftest.py` — a `database_url` fixture that skips DB-dependent
  tests cleanly when Postgres isn't reachable (verified by pointing
  `DATABASE_URL` at a dead port: 5 unit tests still pass, 6 integration
  tests skip).
- `tests/test_schema_constraints.py` — integration tests for the guarantees
  the schema itself is responsible for enforcing: status CHECK constraints
  reject invalid values, `UNIQUE(job_id, stage)` blocks a duplicate result,
  `UNIQUE(job_id, stage, attempt)` allows a new attempt but blocks a repeat,
  FK cascade deletes results when a job is deleted, and the partial index is
  actually used by the query planner (not just present).
- `README.md` — run instructions, service table (API/Postgres/Kafka/Console
  ports), migration workflow, and the directory layout.

**Status at end of Phase 0:** 11 tests passing (5 unit + 6 integration).
Stack runs via `docker compose -f docker/docker-compose.yml up -d`. Stale
worktree branch deleted. Docker containers were left running at the end of
this session — `docker compose -f docker/docker-compose.yml down` to
reclaim resources if needed (the named volume `setu_postgres_data` persists
across `down`, which is what caused the stale-revision issue above; use
`down -v` to also drop it).

---

## Phase 1 — Outbox + Guaranteed Delivery

### 2026-07-20 — `1d0621d` Phase 1: outbox pattern, guaranteed delivery, idempotent job submission

Populated `repositories/`, `services/`, `messaging/`, and `api/routes/`
against the Phase 0 models. Two invariants carry the whole "guaranteed
delivery" claim:

- **Atomic write.** `JobSubmissionService.submit()`
  (`backend/services/job_submission_service.py`) inserts `Job` +
  `OutboxEvent` + `IdempotencyKey` on one session and commits once. There is
  never a `Job` without its `OutboxEvent`. The outbox event's `topic` is set
  to the workflow's first stage name (`workflow[0]`) — dispatch stays data,
  not a hardcoded chain, consistent with the Phase 4 workflow-engine plan.
- **Publish-after-ack.** `OutboxPublisher.poll_once()`
  (`backend/messaging/outbox_publisher.py`) marks an event `published` only
  after `producer.send_and_wait()` returns. If the process dies in between,
  the event is still `pending` and gets republished on the next poll — a
  duplicate downstream is expected (idempotent consumers own that, via
  `Result.UNIQUE(job_id, stage)` from Phase 0); a lost event is not.

Idempotency is enforced by the database, not a check-then-insert race:
`IdempotencyKey.key` is the primary key, so a concurrent duplicate loses the
insert and its `IntegrityError` is caught and turned into "return the
existing job." Same key with a different request body is rejected as a 422
conflict. Verified with a genuine two-coroutine concurrent-insert race (not
a simulated one) — both submissions resolve to the same job, exactly one
`IdempotencyKey` row exists.

The publisher connects to Kafka lazily from its background task
(`OutboxPublisher.ensure_started()`), not during app startup — a Kafka
outage at boot doesn't block the API; jobs still land in the outbox and
drain once the broker is reachable. This was also necessary for testability:
an eager `producer.start()` in the lifespan would make even the plain
`/health` check depend on Kafka being up.

Added `POST /jobs` (requires an `Idempotency-Key` header) and `GET
/jobs/{id}` (`backend/api/routes/jobs.py`).

**Test coverage:** atomicity and the real idempotency race against Postgres
(`tests/test_job_submission_service.py`); publish-after-ack and
failure-leaves-pending using a fake producer, plus a real Kafka round-trip
(`tests/test_outbox_publisher.py`); full HTTP tests including the
outbox-to-Kafka delivery chain (`tests/test_jobs_api.py`).

**A real bug surfaced and fixed during this phase, worth remembering:**
`backend.database.session.get_engine()` is a process-wide singleton
(`@lru_cache`), which is correct for production (one process, one event
loop) but broke under a function-scoped `TestClient` fixture — each test was
spinning a fresh portal thread/event loop while the app kept reusing that
one cached engine's connection pool across all of them. A connection opened
under test 1's (now-dead) loop would later get reused and torn down under
test 3's different loop, which asyncpg can't do. Symptoms were inconsistent
by design: an outright hang in one run, a cascade of "Event loop is closed"
failures starting partway through the suite in another — both from the same
root cause. Fixed by scoping the `TestClient` fixture to the test module
instead of each test function, matching how the app actually runs (started
once, serves many requests). Two smaller false leads were tried and
discarded first: switching pytest-asyncio's loop scope to `session` (didn't
address the actual cause), and having test cleanup fixtures share the app's
cached engine directly (made it worse — that's what caused the hang, since
it added a *second* concurrent live event loop touching the same pool).

Also seeded 5,000 dummy rows in `test_unpublished_outbox_partial_index_is_used`
after the table was cleaned to empty during this debugging — Postgres's
planner correctly prefers a seq scan over the partial index on a near-empty
table, so the test needed to reproduce the actual scenario the index exists
for (many published rows, a few unpublished ones) rather than assert
index-usage unconditionally.

No `docker-compose.yml` changes this phase — Postgres and Redpanda from
Phase 0 cover everything Phase 1 needed.

**Status at end of Phase 1:** 26 tests passing. Database confirmed empty of
test residue after a full run.

---

## Phase 2 — Reliability

### 2026-07-20 — `881e8a9` fix: cap outbox publish retries so a poison event can't loop forever

A live incident, not planned work: while restarting the API server for
Phase 2, it immediately spammed ~33,000 log lines in seconds. Root cause: a
job submitted through Swagger earlier (during the manual-testing walkthrough)
used `workflow: ["Frame Extraction"]` — capitalized, with a space, an
invalid Kafka topic name. That `OutboxEvent` had been sitting `pending`
ever since, retried on every publisher poll with **no cap and no backoff**,
because `fetch_unpublished_batch()` only ever filters on `status ==
PENDING` and nothing ever moved a permanently-failing event out of that
state — despite `OutboxStatus.FAILED` already existing in the enum from
Phase 0, unused until now.

Fixed: `OutboxRepository.mark_failed()` now takes `max_attempts` and
transitions the event to `FAILED` once reached, in the same atomic
`UPDATE` that increments `attempts` (via `sa.case()`, avoiding a
read-then-write race). New `Settings.outbox_max_publish_attempts` (default
10). Regression test (`test_permanently_failing_event_stops_after_max_attempts`)
reproduces the exact scenario and confirms it stops.

### 2026-07-20 — `9f8ff1d` Phase 2 (part 1): dummy worker, idempotent processing, crash recovery

Built the crash-safe spine first, deliberately before retry/backoff or DLQ
(per plan — prove the reliable core, then layer failure handling on top).

- **A worker is a real, standalone OS process**
  (`python -m backend.workers.cli <topic>`), not a background task inside
  the API. This was a deliberate choice, not a default: the crash-recovery
  guarantee has to survive an actual kill of just the worker, and asyncio
  task cancellation running inside the API process isn't a real crash.
- **Offset-commit ordering is the entire guarantee.** Per message: consume
  → `StageProcessingService.handle()` commits `Result` + `WorkerExecution`
  + `Job` status to Postgres → only *then* does the harness
  (`backend/workers/runner.py`) commit the Kafka offset
  (`enable_auto_commit=False`). A crash before the Postgres commit loses
  nothing (offset was never committed, Kafka redelivers). A crash after the
  Postgres commit but before the offset commit causes redelivery into a
  handler that finds `Result` already exists for `(job_id, stage)` and
  treats it as a no-op — idempotent, not duplicated.
- **The retry budget lives in Postgres** (`Job.attempts`, read fresh on
  every delivery), not as an in-memory loop variable — an in-memory counter
  would reset to zero on every crash, and a permanently-failing message
  would never reach a DLQ.
- `DummyWorker` does no real work; it exists purely to exercise the
  harness. Two payload flags enable deterministic testing: `_hang_seconds`
  (sleep before finishing, so a test can kill the process at a known point
  mid-processing) and `_fail` (for the retry/DLQ work still to come).
- Outbox events now carry an explicit `"stage"` index rather than the
  worker assuming 0 — true today since only `workflow[0]` is ever
  dispatched, but hardcoding it would be a latent bug once Phase 4's engine
  dispatches other stages.
- **Scope for this part:** a job is marked `COMPLETED` only when its
  dispatched stage is the *last* one in the workflow list; otherwise it's
  left `RUNNING`. There's no engine yet to advance to the next stage — use
  single-stage workflows for now so "stage done" and "job done" mean the
  same thing.

**The actual deliverable:** `tests/test_worker_crash_recovery.py`. Spawns
the worker CLI as a genuine OS subprocess, hard-kills it while `DummyWorker`
is deliberately hanging mid-processing (well before any DB write or offset
commit), restarts an identical worker on the same consumer group, and
asserts: zero `Result`/`WorkerExecution` rows immediately after the kill,
job still `pending`; exactly one of each after recovery, job `completed`,
`attempts == 1`. Passes in ~22s.

**Status: 28 tests passing.** Database confirmed clean after a full run.

### 2026-07-21 — Phase 2 (part 2): exponential backoff + dead-letter queue

`StageProcessingService.handle()` now returns a `ProcessingResult` (outcome
+ attempt/max_attempts/error) instead of raising or silently returning —
raising couldn't distinguish "still has retry budget" from "budget
exhausted, DLQ it," and both needed different harness behavior.
`ProcessingOutcome`: `SUCCEEDED`, `ALREADY_DONE` (idempotent redelivery
no-op), `RETRY`, `EXHAUSTED`. Failure now routes through
`_record_failure()`, which compares `attempt` to `Job.max_attempts` (both
in Postgres, per the Phase 2 part 1 rule about not trusting an in-memory
counter) and marks the job `DEAD_LETTERED` when the budget is spent.

`WorkerRunner` acts on the outcome: `RETRY` sleeps for
`base_delay * 2^(attempt-1)` (capped at `retry_max_delay_seconds`,
new `Settings` fields) and leaves the offset uncommitted; `EXHAUSTED`
publishes the message to `<topic>.dlq` (direct produce, not the outbox —
this is a worker-internal failure record, not a domain event) and commits
the offset to unblock the partition. `WorkerRunner.consume_one()` is a new
method, split out of `run_forever()`'s loop body, so
`tests/test_worker_retry_and_dlq.py` can drive one delivery at a time and
assert state between them.

**Bug found by the test, not by inspection:** the first version of
`test_failing_job_retries_then_dead_letters` hung indefinitely on the
second `consume_one()` call. Root cause: "leave the offset uncommitted so
Kafka redelivers" is only true across a *consumer restart* — that's the
mechanism the crash-recovery test exercises. Within one live
`AIOKafkaConsumer` instance, the local fetch position advances on every
`getone()` regardless of whether the offset was committed; not committing
just means a *future* restart would refetch from the last commit. Since
`run_forever()` runs a single long-lived consumer for the worker's entire
life, the original RETRY path would have silently skipped a failed message
forever instead of retrying it — never actually reprocessing anything,
just quietly losing failed jobs. Confirmed with a standalone script before
touching the fix: one `consume_one()` call worked, a second hung waiting
for a message that would never arrive. Fix: `RETRY` now explicitly
`consumer.seek()`s back to the failed record's offset before returning, so
the next `getone()` re-fetches the same message. Verified the fix with the
same standalone repro (3 calls now correctly produce attempt 1 → retry,
attempt 2 → retry, attempt 3 → exhausted/DLQ) before rerunning the suite.

`tests/test_worker_retry_and_dlq.py` — two tests: full retry → DLQ →
partition-unblocked cycle (asserts a DLQ message lands with the right
payload, and that a fresh healthy message on the same topic afterward is
processed promptly rather than stuck behind the poison one), and a timing
test confirming backoff actually grows between attempts rather than just
"retries happen at all."

Also found ~1hr of stale test data (three `running` Jobs from
`retry-dlq-*` test topics, no matching `results`/`worker_executions`)
left behind by the killed hung run — its `finally` cleanup never executed
because the process was killed mid-`await`, not exited normally. Deleted
manually; not a code bug, just a reminder that killing a test mid-run
skips its cleanup same as any other crash.

**Status: 30 tests passing.** Database confirmed clean after a full run.

### Next up

Phase 3 (observability): structured logging, OpenTelemetry tracing,
Prometheus metrics, Grafana dashboards.
