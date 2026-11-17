"""质量隔离入口：原因码分布、隔离明细、隔离 CSV 导出。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from app import errors
from app.modules.m3_quality import quarantine, service
from app.web import state

router = APIRouter()


def _current_payload(request: Request) -> tuple[dict, list[dict]]:
    app_state = state(request)
    run = app_state.current_run()
    if run is None:
        raise errors.NotFound("还没有运行记录", code="E_NO_RUN")
    entries = app_state.store.load_quarantine(run["run_id"])
    return run, entries


@router.get("/api/quarantine")
def get_quarantine(request: Request, limit: int = 200):
    run, entries = _current_payload(request)
    limited = entries[: max(1, min(limit, 1000))]
    return {
        "run_id": run["run_id"],
        "total": len(entries),
        "reason_counts": quarantine.group_counts(entries),
        "accounts": run["summary"].get("accounts", {}),
        "checks": service.account_checks(run["summary"].get("accounts", {})),
        "entries": limited,
    }


@router.get("/api/quarantine.csv")
def export_quarantine(request: Request):
    run, entries = _current_payload(request)
    body = quarantine.to_csv(entries)
    return PlainTextResponse(
        body, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="quarantine-{run["run_id"]}.csv"'})
