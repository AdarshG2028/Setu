# Setu

Event-driven backend platform for orchestrating long-running AI media
workflows. The deliverable is the distributed execution engine — AI models are
plugins that workers run, not the point of the system.

## Requirements

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- Docker (for Postgres and the Kafka-compatible broker only)

Python runs on the host via uv; Docker is used for backing services.

## Getting started

```bash
uv sync
cp .env.example .env

# Postgres + Redpanda + Redpanda Console + Prometheus + Grafana
docker compose -f docker/docker-compose.yml up -d

uv run alembic upgrade head
uv run uvicorn backend.api.main:app --reload

# In another terminal: at least one worker, so submitted jobs get processed.
uv run python -m backend.workers.cli <topic>
```

| Service | Address |
| --- | --- |
| API | http://localhost:8000 (docs at `/docs`, metrics at `/metrics`) |
| Postgres | `localhost:5432` (`setu` / `setu` / `setu`) |
| Kafka API | `localhost:19092` from the host, `redpanda:9092` inside compose |
| Redpanda Console | http://localhost:8081 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 ("Setu — Pipeline Overview" dashboard, anonymous admin access) |

Redpanda is used instead of Kafka + ZooKeeper: it speaks the Kafka protocol
but runs as a single process, which keeps broker- and worker-kill testing
simple.

Each worker process exposes its own metrics on `--metrics-port` (default
`9100`) — a worker is an independent OS process with its own in-memory
metrics registry, so Prometheus scrapes it as a separate target from the
API. Running a second worker on the same host needs a different
`--metrics-port`.

## Tests

```bash
uv run pytest
```

Tests that need Postgres skip automatically when the stack is down.

## Migrations

The database URL comes from `backend.core.config`, not `alembic.ini`, so
`DATABASE_URL` is the single source of truth.

```bash
uv run alembic revision --autogenerate -m "description"   # needs Postgres up
uv run alembic upgrade head
uv run alembic check                                      # fail on model drift
```

New models must be re-exported from `backend/models/__init__.py`, or
autogenerate will not see them.

## Layout

```
backend/
├── api/            FastAPI routers, schemas, dependencies
├── core/           settings and application config
├── database/       engine, session, Alembic migrations
├── models/         SQLAlchemy ORM models
├── repositories/   data access
├── services/       business logic
├── workers/        independent worker services
├── workflow/       workflow engine that dispatches stages
├── planner/        natural language prompt to workflow JSON
├── storage/        media artifact storage
├── messaging/      Kafka producers, consumers, outbox publisher
├── observability/  logging, tracing, metrics
└── shared/         shared types and helpers
```
