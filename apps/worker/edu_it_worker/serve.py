"""Worker entrypoint: real lab consumers plus the health/readiness surface.

Run with ``python -m edu_it_worker.serve``. :func:`build_served_worker` composes the lab
worker (:func:`~edu_it_worker.labs.composition.build_lab_worker`) over the real adapters:
PostgreSQL lab repositories, the Redis task queue, the approved lab content and the
sandbox provider selected by ``SANDBOX_PROVIDER`` (``docker`` is development only and is
refused with ``EDU_IT_ENV=production``). The lab Config is validated before any provider
call, so an invalid Config exits without creating a Sandbox.

Environment: ``EDU_IT_CONTENT_ROOT`` (default ``content``) is the authored content root and
``EDU_IT_BUNDLE_ROOT`` (default: current directory) is the repository root that the lab
``setup_script`` / ``check_bundle`` paths are relative to.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn
from edu_it_content.lab_runtime import load_synced_store, setup_script_resolver
from edu_it_infrastructure.adapters.lab_catalog import Content_LabCatalog
from edu_it_infrastructure.composition import CompositionSettings
from edu_it_infrastructure.lab_composition import build_lab_ports

from edu_it_worker.app import create_health_app
from edu_it_worker.labs import LabWorker, build_lab_worker

__all__ = ["build_served_worker", "main"]


def build_served_worker(
    *, settings: CompositionSettings | None = None, poll_interval_s: float = 1.0
) -> LabWorker:
    """Compose the lab worker over the real adapters (nothing is started)."""
    settings = settings if settings is not None else CompositionSettings.from_env()
    ports = build_lab_ports(settings)
    bundle_root = Path(os.environ.get("EDU_IT_BUNDLE_ROOT", ".")).resolve()
    content_root = os.environ.get("EDU_IT_CONTENT_ROOT", "content")
    store, _report = load_synced_store(content_root)
    return build_lab_worker(
        queue=ports.queue,
        sessions=ports.sessions,
        runs=ports.runs,
        mastery=ports.mastery,
        catalog=Content_LabCatalog(store),
        clock=ports.clock,
        bundle_root=bundle_root,
        settings=settings,
        setup_scripts=setup_script_resolver(store, bundle_root),
        poll_interval_s=poll_interval_s,
    )


def main() -> None:
    """Start the lab consumers and serve the health surface until interrupted."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper())
    worker = build_served_worker()
    health_app = create_health_app()
    worker.start()
    try:
        uvicorn.run(
            health_app,
            host=os.environ.get("WORKER_HEALTH_HOST", "127.0.0.1"),
            port=int(os.environ.get("WORKER_HEALTH_PORT", "8001")),
        )
    finally:
        worker.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
