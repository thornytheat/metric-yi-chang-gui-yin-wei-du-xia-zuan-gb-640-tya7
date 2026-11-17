"""报告与导出入口：报告结构、四类文件导出。"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse, Response

from app.modules.m6_export import service
from app.web import state

router = APIRouter()


@router.get("/api/report")
def get_report(request: Request, run_id: str = Query(default="")):
    app_state = state(request)
    return service.report(app_state.settings, app_state.store, run_id or None)


@router.get("/api/export/{kind}")
def export_file(request: Request, kind: str, run_id: str = Query(default="")):
    app_state = state(request)
    body, media_type, filename = service.export_text(
        app_state.settings, app_state.store, kind, run_id or None)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if media_type.startswith("text/csv"):
        return PlainTextResponse(body, media_type=media_type, headers=headers)
    return Response(content=body, media_type=media_type, headers=headers)
