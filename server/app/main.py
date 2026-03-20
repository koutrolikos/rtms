from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server.app.api.agent import router as agent_router
from server.app.api.operator import router as operator_router
from server.app.core.config import get_settings
from server.app.core.logging import configure_logging
from server.app.db.session import init_db


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    app = FastAPI(title="RF Range-Test MVP")
    app.include_router(agent_router)
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
    uvicorn.run("server.app.main:app", host=settings.host, port=settings.port, reload=False)

