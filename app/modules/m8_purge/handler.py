"""门店退网清除入口：发起/续跑清除、任务状态、凭据与报告导出。

清除优先于导入：导入入口在写库前检查 purges 表，存在 running 任务直接 409。
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from app import errors
from app.modules.m8_purge import csv_out, service
from app.web import state

router = APIRouter()


@router.get("/api/purges")
def list_purges(request: Request):
    return service.status_payload(state(request).store)


@router.get("/api/purges/{purge_id}")
def get_purge(request: Request, purge_id: str):
    store = state(request).store
    purge = store.get_purge(purge_id)
    if purge is None:
        raise errors.NotFound("清除任务不存在：" + purge_id, code="E_PURGE_NOT_FOUND")
    payload = service._finished_payload(purge, store, reused=False)
    return payload


@router.post("/api/purges")
async def create_purge(request: Request):
    app_state = state(request)
    body: dict = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        body = await request.json() or {}
    hotel_code = (body.get("hotel_code") or "").strip()
    if not hotel_code:
        raise errors.BadInput("门店编码不能为空", code="E_BAD_HOTEL")
    return service.purge_hotel(app_state.settings, app_state.store, hotel_code)


@router.get("/api/purges/{purge_id}/vouchers/points.csv")
def export_point_vouchers(request: Request, purge_id: str):
    store = state(request).store
    purge = store.get_purge(purge_id)
    if purge is None:
        raise errors.NotFound("清除任务不存在：" + purge_id, code="E_PURGE_NOT_FOUND")
    body = csv_out.point_vouchers_csv(store.load_point_vouchers(purge_id))
    return PlainTextResponse(
        body, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="purge-point-vouchers-{purge_id}.csv"'})


@router.get("/api/purges/{purge_id}/vouchers/quarantine.csv")
def export_quarantine_vouchers(request: Request, purge_id: str):
    store = state(request).store
    purge = store.get_purge(purge_id)
    if purge is None:
        raise errors.NotFound("清除任务不存在：" + purge_id, code="E_PURGE_NOT_FOUND")
    body = csv_out.quarantine_vouchers_csv(store.load_quarantine_vouchers(purge_id))
    return PlainTextResponse(
        body, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="purge-quarantine-vouchers-{purge_id}.csv"'})
