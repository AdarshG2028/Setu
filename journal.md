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

Not started. Scope per the roadmap: `POST /jobs` writes a `Job` +
`OutboxEvent` in one transaction; an Outbox Publisher polls unpublished
events and publishes them to Kafka; idempotency key enforcement on job
submission.
