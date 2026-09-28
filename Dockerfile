FROM python:3.12-slim

WORKDIR /workspace

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        gdal-bin \
        libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock ./

# Dependencies only: the source isn't in the image (the workspace is
# mounted at runtime). `uv run` / `uv sync` then installs networkforge
# itself, editable, from the mounted source.
RUN uv sync --dev --no-install-project

ENV PATH="/workspace/.venv/bin:$PATH"
# Lets plain `python` import networkforge before the first `uv sync`.
ENV PYTHONPATH="/workspace/src"