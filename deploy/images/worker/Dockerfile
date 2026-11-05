# syntax=docker/dockerfile:1.7
#
# worker image (Req 12.3, 12.4; ADR 0011).
#
#   docker build -f deploy/images/worker/Dockerfile .
#
# The build context is the repository root. The final stage is distroless
# `nonroot` (no shell, no package manager) and runs as numeric user 65532.
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

COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker

RUN uv sync --frozen --no-dev --no-editable --package edu-it-worker

# -- worker (final) -----------------------------------------------------------
FROM gcr.io/distroless/python3-debian13:nonroot AS worker

COPY --from=builder /opt/venv /opt/venv
# Lab setup_script / check_bundle paths are relative to the bundle root (/app).
COPY content /app/content
# Content sync validates `lab.image` against <repo>/lab-images/<name>; the repo root is found as the
# nearest ancestor of EDU_IT_CONTENT_ROOT (/app/content) that holds lab-images, so it lives at /app.
COPY lab-images /app/lab-images
# The AI worker loads its versioned prompts at startup (Glossary_Prompt); the installed package
# cannot find <repo>/prompts by path, so the directory is copied and named by EDU_IT_PROMPTS_ROOT.
COPY prompts /app/prompts

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EDU_IT_CONTENT_ROOT=/app/content \
    EDU_IT_PROMPTS_ROOT=/app/prompts \
    EDU_IT_BUNDLE_ROOT=/app \
    WORKER_HEALTH_HOST=0.0.0.0 \
    WORKER_HEALTH_PORT=8001

WORKDIR /app
USER 65532:65532

EXPOSE 8001
ENTRYPOINT ["/opt/venv/bin/python", "-m", "edu_it_worker.serve"]
