"""日历与归一规则入口：规则清单、单行试算。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app import config, errors
from app.modules.m2_calendar import contract, service
from app.modules.m2_calendar.calendar_rules import day_stratum
from app.web import state

router = APIRouter()


@router.get("/api/contract")
def get_contract(request: Request):
    app_state = state(request)
    settings = app_state.settings
    payload = contract.payload()
    payload["window"] = {
        "points": settings.window_points,
        "min_baseline": settings.min_baseline_points,
        "z_threshold": str(settings.z_threshold),
        "confirm_days": settings.confirm_days,
        "late_days_allowed": settings.late_days_allowed,
        "late_window_days": settings.late_window_days,
        "eval_date": settings.eval_date.isoformat(),
        "rules_version": settings.rules_version,
    }
    payload["holidays"] = sorted(day.isoformat() for day in config.HOLIDAYS)
    return payload


@router.post("/api/contract/preview")
async def preview_row(request: Request):
    app_state = state(request)
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise errors.BadInput("请求体不是 JSON", code="E_BAD_JSON") from exc
    row = body.get("row") if isinstance(body, dict) else None
    if not isinstance(row, dict):
        raise errors.BadInput("缺少 row 对象", code="E_BAD_ROW")
    row = dict(row)
    row.setdefault("_line_no", 0)
    coerced, rejects = service.coerce_row(row, app_state.settings)
    return {
        "ok": coerced is not None and not rejects,
        "stratum": day_stratum(coerced.biz_date) if coerced else "",
        "row": None if coerced is None else {
            "hotel_code": coerced.hotel_code,
            "channel": coerced.channel,
            "biz_date": coerced.biz_date.isoformat(),
            "stratum": coerced.stratum,
            "rooms_available": coerced.rooms_available,
            "rooms_sold": coerced.rooms_sold,
            "adr": str(coerced.adr),
            "revenue_net": str(coerced.revenue_net),
            "snapshot_local": coerced.snapshot_local.isoformat(),
            "late_days": coerced.late_days,
        },
        "rejects": rejects,
    }
