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

RUN uv sync --dev

COPY src ./src
COPY tests ./tests

ENV PATH="/workspace/.venv/bin:$PATH"
ENV PYTHONPATH="/workspace/src"

CMD ["pytest", "-v"]