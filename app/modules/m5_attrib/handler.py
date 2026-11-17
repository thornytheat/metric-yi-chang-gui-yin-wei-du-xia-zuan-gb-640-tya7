"""归因与批次入口：批次历史、归因结果、同源重跑、四账。"""
from __future__ import annotations

import os

from fastapi import APIRouter, Query, Request

from app import errors
from app.modules.m5_attrib import batches, service
from app.web import state

router = APIRouter()
DIMENSIONS = ("hotel_code", "channel")


@router.get("/api/batches")
def list_batches(request: Request, limit: int = 20):
    app_state = state(request)
    runs = app_state.store.list_runs(limit=max(1, min(limit, 100)))
    return {
        "runs": batches.history(runs, limit),
        "latest": runs[0]["run_id"] if runs else "",
    }


@router.get("/api/batches/{run_id}")
def get_batch(request: Request, run_id: str):
    run = state(request).store.get_run(run_id)
    if run is None:
        raise errors.NotFound("批次不存在：" + run_id, code="E_BATCH_NOT_FOUND")
    return {
        "run_id": run["run_id"],
        "manifest": run["manifest"],
        "summary": run["summary"],
        "key_parts": batches.key_parts(run["run_key"]),
        "account_notes": batches.account_notes(run["summary"].get("accounts", {})),
    }


@router.get("/api/attribution")
def get_attribution(
    request: Request,
    dimension: str = Query(default="hotel_code"),
    cur_days: int = Query(default=7),
    base_days: int = Query(default=7),
    run_id: str = Query(default=""),
):
    if dimension not in DIMENSIONS:
        raise errors.BadInput("维度只支持 hotel_code 或 channel", code="E_BAD_DIMENSION")
    if not (1 <= cur_days <= 60) or not (1 <= base_days <= 120):
        raise errors.BadInput("窗口天数超出范围：当期 1-60，基期 1-120", code="E_BAD_WINDOW")
    app_state = state(request)
    result = service.attribution(app_state.settings, app_state.store,
                                run_id or None, dimension, cur_days, base_days)
    if not result:
        raise errors.NotFound("还没有运行记录", code="E_NO_RUN")
    return result


@router.get("/api/reconcile")
def get_reconcile(request: Request, run_id: str = Query(default="")):
    app_state = state(request)
    result = service.reconcile(app_state.settings, app_state.store, run_id or None)
    if not result:
        raise errors.NotFound("还没有运行记录", code="E_NO_RUN")
    return result


@router.post("/api/attribution/rerun")
async def rerun_batch(request: Request):
    app_state = state(request)
    body: dict = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        body = await request.json() or {}
    name = body.get("sample") or app_state.settings.default_sample
    if os.path.basename(name) != name:
        raise errors.BadInput("样例名不合法", code="E_BAD_SAMPLE")
    path = os.path.join(app_state.settings.sample_dir, name)
    if not os.path.isfile(path):
        raise errors.NotFound("样例不存在：" + name, code="E_SAMPLE_NOT_FOUND")
    with open(path, "rb") as handle:
        data = handle.read()
    summary = service.run_batch(app_state.settings, app_state.store, data, name)
    return {
        "run_id": summary["run_id"],
        "reused": summary["reused"],
        "accounts": summary["accounts"],
        "checks": summary["checks"],
    }
