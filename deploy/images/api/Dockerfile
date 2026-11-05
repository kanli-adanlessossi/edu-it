# syntax=docker/dockerfile:1.7
#
# api and migrations images (Req 12.3, 12.4; ADR 0011).
#
# One Dockerfile, two final targets built from the same builder stage:
#   docker build -f deploy/images/api/Dockerfile --target api        .   (default)
#   docker build -f deploy/images/api/Dockerfile --target migrations .
#
# The build context is the repository root. The final stages are distroless
# `nonroot` (no shell, no package manager) and run as numeric user 65532.
# Base image versions are pinned per docs/adr/0001-versions.md.

ARG UV_VERSION=0.9.7

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# -- builder ------------------------------------------------------------------
# Debian 13 (trixie) ships Python 3.13, the same interpreter as the distroless
# python3-debian13 final stage, so the virtual environment is valid there.
FROM debian:13-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON=/usr/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /src

# The uv workspace needs every member's pyproject.toml; sources are installed
# non-editable so the venv is self-contained.
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker

RUN uv sync --frozen --no-dev --no-editable --package edu-it-api

# -- common runtime base ------------------------------------------------------
FROM gcr.io/distroless/python3-debian13:nonroot AS runtime-base

COPY --from=builder /opt/venv /opt/venv

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
USER 65532:65532

# -- migrations ---------------------------------------------------------------
FROM runtime-base AS migrations

ENTRYPOINT ["/opt/venv/bin/python", "-m", "edu_it_infrastructure.migrate"]

# -- api (default target: last stage) -----------------------------------------
FROM runtime-base AS api

COPY content /app/content
# Same repo-root layout as the worker image: content validation looks for /app/lab-images/<name>.
COPY lab-images /app/lab-images
ENV EDU_IT_CONTENT_ROOT=/app/content \
    API_HOST=0.0.0.0 \
    API_PORT=8000

EXPOSE 8000
ENTRYPOINT ["/opt/venv/bin/python", "-m", "edu_it_api.serve"]
