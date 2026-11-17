"""应用装配：存储、种子批次、领域异常映射、模块路由注册。"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config, errors
from app.modules.m1_ingest import handler as ingest_handler
from app.modules.m2_calendar import handler as calendar_handler
from app.modules.m3_quality import handler as quality_handler
from app.modules.m4_detect import handler as detect_handler
from app.modules.m5_attrib import handler as attrib_handler
from app.modules.m6_export import handler as export_handler
from app.modules.m7_review import handler as review_handler
from app.store import Store
from app.web import AppState, pages


def create_app(settings: config.Settings | None = None) -> FastAPI:
    active = settings or config.Settings()
    app = FastAPI(title="指标异常归因与维度下钻", docs_url="/docs", redoc_url=None)
    store = Store(active.db_path)
    app_state = AppState(settings=active, store=store)
    app.state.anomalyattr = app_state
    if active.seed_on_start:
        app_state.ensure_seed(active)

    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.add_exception_handler(errors.DomainError, errors.domain_error_handler)

    @app.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:  # noqa: ANN202
        return JSONResponse(status_code=400, content={"error": "E_VALUE", "message": str(exc)})

    app.include_router(ingest_handler.router)
    app.include_router(calendar_handler.router)
    app.include_router(quality_handler.router)
    app.include_router(detect_handler.router)
    app.include_router(attrib_handler.router)
    app.include_router(export_handler.router)
    app.include_router(review_handler.router)
    app.include_router(pages)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "rules_version": active.rules_version,
                "db_bytes": os.path.getsize(active.db_path) if os.path.exists(active.db_path) else 0}

    return app
