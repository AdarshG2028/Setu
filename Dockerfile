FROM python:3.13-slim

# ffmpeg is a runtime dependency, not a build tool: video_analysis probes
# with ffprobe, and every Phase 5 media capability shells out to ffmpeg
# directly (see backend/workers/media.py -- there is no Python wrapper
# package to pull in via uv, so it cannot come from pyproject.toml).
# Without it those workers raise MediaProcessingError on every message and
# retry until the budget is spent.
#
# Installed before COPY so this layer -- the slowest in the build -- is
# cached across code changes rather than reinstalled on every commit.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev

# Run the venv's binaries directly rather than through `uv run`.
#
# `uv run` re-checks the environment against pyproject.toml before every
# command, and without --no-dev it considers the dev group part of that:
# starting this container used to install pytest and friends *at boot*,
# undoing the --no-dev above. Worse, it made startup depend on PyPI being
# reachable -- a network blip or a locked-down VPC would stop a container
# whose dependencies were already baked into the image.
#
# Shell form (not exec form) so ${PORT} expands; Render sets it, local
# runs fall back to 8000. `exec` on the final command replaces the shell
# so SIGTERM reaches uvicorn directly and it shuts down gracefully,
# instead of being delivered to /bin/sh and ignored.
CMD .venv/bin/alembic upgrade head && exec .venv/bin/uvicorn backend.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
