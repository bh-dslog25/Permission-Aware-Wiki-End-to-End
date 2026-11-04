"""FastAPI entry point for the Permission-Aware RAG Wiki MVP."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import audit as audit_api
from .api import admin as admin_api
from .api import feedback as feedback_api
from .api import owner as owner_api
from .api import query as query_api
from .audit import AuditLogger
from .authz.policy_adapter import PolicyAdapter
from .config import Settings, get_settings
from .db import Database
from .embedding import build_embedder
from .rag.pipeline import RagPipeline, build_default_pipeline

logger = logging.getLogger(__name__)


@dataclass
class Container:
    pipeline: RagPipeline
    audit: AuditLogger
    db: Database


def build_container(settings: Settings) -> Container:
    db = Database(settings.database_url)
    db.open()
    audit = AuditLogger(db)
    pipeline = build_default_pipeline(
        db=db,
        embedder=build_embedder(),
        policy_adapter=PolicyAdapter(api_url=settings.openfga_api_url),
        audit=audit,
    )
    return Container(pipeline=pipeline, audit=audit, db=db)


def create_app(*, container: Container | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = container or build_container(get_settings())
        app.state.container = c
        try:
            yield
        finally:
            c.db.close()

    app = FastAPI(
        title="Permission-Aware RAG Wiki",
        version="0.1.0",
        description="Hỏi đáp tài liệu nội bộ có kiểm soát quyền, hiệu lực và citation.",
        lifespan=lifespan,
    )
    origins = [o for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:8443,http://localhost:3000").split(",") if o]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error")
        return JSONResponse(status_code=500, content={"detail": "Lỗi hệ thống"})

    app.include_router(query_api.router)
    app.include_router(audit_api.router)
    app.include_router(admin_api.router)
    app.include_router(feedback_api.router)
    app.include_router(owner_api.router)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
