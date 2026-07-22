FROM python:3.13-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev

# Render sets $PORT for web services; default to 8000 for local/manual runs.
# Shell form (not exec form) so $PORT actually expands.
CMD uv run alembic upgrade head && uv run uvicorn backend.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
