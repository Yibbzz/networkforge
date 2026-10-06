FROM python:3.12-slim

WORKDIR /workspace

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        gdal-bin \
        libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# The container's environment lives in /opt/venv, not /workspace/.venv.
# /workspace is the host's project folder, mounted at runtime: a .venv
# there is shared with the host, whose uv uses a different Python and so
# throws it away and rebuilds it (and the container's uv does the same
# back) - packages vanished mid-run. Each side now has its own.
# UV_LINK_MODE=copy: uv's cache and the environment are on different
# filesystems, so hard links fail (uv warned on every run).
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./

# Dependencies only: the source isn't in the image (the workspace is
# mounted at runtime). `uv sync` (postCreateCommand in
# .devcontainer/devcontainer.json) then installs networkforge itself,
# editable, from the mounted source.
RUN uv sync --dev --no-install-project

ENV PATH="/opt/venv/bin:$PATH"
# Lets plain `python` import networkforge before the first `uv sync`.
ENV PYTHONPATH="/workspace/src"