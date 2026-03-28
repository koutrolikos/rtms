from __future__ import annotations

import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from rtms.server.app.api.host import router as host_router
from rtms.server.app.api.operator import router as operator_router
from rtms.server.app.core.auth import install_basic_auth
from rtms.server.app.core.config import get_settings
from rtms.server.app.core.logging import configure_logging
from rtms.server.app.db.session import init_db

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    app = FastAPI(title="RTMS")
    install_basic_auth(app, settings)
    app.include_router(host_router)
    app.include_router(operator_router)
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
        name="static",
    )
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    logger.info(
        "Starting server on listen address http://%s:%s with public URL %s",
        settings.host,
        settings.port,
        settings.effective_public_base_url,
    )
    uvicorn.run("rtms.server.app.main:app", host=settings.host, port=settings.port, reload=False)
