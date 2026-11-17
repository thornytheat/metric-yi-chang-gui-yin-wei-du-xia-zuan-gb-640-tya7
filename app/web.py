"""页面层：模板环境、格式化过滤器、六个页面路由与批次上下文。

页面只做展示，所有判定都在各模块 service 里完成；这里不做任何计算。
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import config, reasons
from app.modules.m2_calendar import contract
from app.modules.m3_quality import service as quality_service
from app.modules.m4_detect import service as detect_service
from app.modules.m5_attrib import batches, service as attrib_service
from app.modules.m6_export import service as export_service
from app.modules.m7_review import service as review_service
from app.store import Store

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
templates = Jinja2Templates(directory=TEMPLATE_DIR)

NAV = (
    ("/", "接入", "index"),
    ("/anomalies", "异常", "anomalies"),
    ("/review", "复盘", "review"),
    ("/attribution", "分解", "attribution"),
    ("/drilldown", "下钻", "drilldown"),
    ("/batches", "批次", "batches"),
    ("/report", "报告", "report"),
)


@dataclass
class AppState:
    settings: config.Settings
    store: Store

    def current_run(self) -> dict | None:
        return self.store.latest_run()

    def ensure_seed(self, settings: config.Settings | None = None) -> dict | None:
        """启动时若无批次，用默认样例跑一次；已有批次直接返回，不重复落库。"""
        existing = self.store.latest_run()
        if existing is not None:
            return existing
        active = settings or self.settings
        path = os.path.join(active.sample_dir, active.default_sample)
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as handle:
            data = handle.read()
        attrib_service.run_batch(active, self.store, data, active.default_sample)
        return self.store.latest_run()


def state(request: Request) -> AppState:
    return request.app.state.anomalyattr


def money(value) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return "-"
    return f"{number.quantize(Decimal('0.01')):,.2f}"


def integer(value) -> str:
    try:
        return f"{int(value):,d}"
    except (TypeError, ValueError):
        return "-"


def ratio(value, places: int = 4) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"{Decimal(str(value)):.{places}f}"
    except InvalidOperation:
        return "-"


def percent(value) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"{Decimal(str(value)) * 100:.1f}%"
    except InvalidOperation:
        return "-"


def axis_days(start: str, end: str) -> list[str]:
    begin = dt.date.fromisoformat(start)
    stop = dt.date.fromisoformat(end)
    days = []
    while begin < stop:
        days.append(begin.isoformat())
        begin += dt.timedelta(days=1)
    return days


def axis_for_runs(runs: list[dict], watermark: str, days: list[str] | None = None) -> dict:
    ordered = list(reversed(runs))
    return {
        "runs": [{"label": item.get("watermark") or item["created_at"][:10],
                  "date": item.get("watermark") or "",
                  "sub": item["run_id"],
                  "status": "warn" if item.get("rows_quarantine") else "ok",
                  "magnitude": min(30, item.get("points_valid", 0) / 60)}
                 for item in ordered],
        "days": days or [],
        "watermark": watermark,
    }


def grid_days(run: dict | None) -> list[str]:
    grid = ((run or {}).get("summary", {}) or {}).get("grid", {}) or {}
    start, end = grid.get("start"), grid.get("end")
    if not start or not end:
        return []
    stop = dt.date.fromisoformat(end) + dt.timedelta(days=1)
    return axis_days(start, stop.isoformat())


def axis_for_dates(days: list[str], watermark: str, marks: list[dict] | None = None) -> dict:
    return {"days": days, "watermark": watermark, "marks": marks or []}


def status_text(status: str) -> str:
    return {
        "confirmed": "确认",
        "candidate": "候选",
        "normal": "平稳",
        "insufficient": "基线不足",
        "undefined": "不判",
    }.get(status, status)


def direction_text(direction: str) -> str:
    return {"high": "偏高", "low": "偏低"}.get(direction, "-")


def channel_text(channel: str) -> str:
    return config.CHANNEL_LABELS.get(channel, channel)


def stratum_text(stratum: str) -> str:
    return config.STRATUM_LABELS.get(stratum, stratum)


def factor_text(key: str) -> str:
    return {"rooms": "可售房晚", "occ": "出租率", "adr": "均价"}.get(key, key or "-")


def confidence_text(level: str) -> str:
    return {"high": "高", "medium": "中", "low": "低"}.get(level, level)


def verdict_text(verdict: str) -> str:
    return {"stable": "稳定", "unstable": "翻转"}.get(verdict, verdict)


def relation_text(kind: str) -> str:
    return {"same_hotel": "同门店", "same_channel": "同渠道",
            "chain": "跨店跨渠道"}.get(kind, kind or "-")


def scope_text(scope: str) -> str:
    return {"channel": "渠道", "chain": "连锁"}.get(scope, scope or "-")


templates.env.filters.update({
    "money": money,
    "int": integer,
    "ratio": ratio,
    "percent": percent,
    "status": status_text,
    "direction": direction_text,
    "channel": channel_text,
    "stratum": stratum_text,
    "factor": factor_text,
    "confidence": confidence_text,
    "verdict": verdict_text,
    "relation": relation_text,
    "scope": scope_text,
    "reason": reasons.label,
})

pages = APIRouter()


def _base(request: Request, active: str, run: dict | None) -> dict:
    summary = (run or {}).get("summary", {})
    return {
        "request": request,
        "nav": NAV,
        "active": active,
        "run": run,
        "summary": summary,
        "watermark": summary.get("watermark", ""),
        "rules_version": (run or {}).get("rules_version", config.RULES_VERSION),
        "created_at": (run or {}).get("created_at", ""),
        "tz_label": "UTC+08:00",
        "accounts": summary.get("accounts", {}),
        "checks": summary.get("checks", []),
    }


def _page(request: Request, name: str, active: str, **ctx):
    app_state = state(request)
    run = app_state.current_run()
    if run is not None:
        ctx.setdefault("run", run)
    context = _base(request, active, run)
    context.update(ctx)
    return templates.TemplateResponse(request, name, context)


@pages.get("/", response_class=HTMLResponse)
def page_index(request: Request):
    app_state = state(request)
    run = app_state.current_run()
    samples = []
    for name in sorted(os.listdir(app_state.settings.sample_dir)):
        path = os.path.join(app_state.settings.sample_dir, name)
        if os.path.isfile(path):
            samples.append({"name": name, "bytes": os.path.getsize(path)})
    runs = app_state.store.list_runs(limit=8)
    detection = {}
    if run is not None:
        points = attrib_service.load_view(app_state.store, run["run_id"])
        detection = detect_service.detect(points, app_state.settings)["summary"]
    history = batches.history(runs, 8)
    return _page(request, "index.html", "index", axis_kind="run",
                 axis=axis_for_runs(history, summary_watermark(run), grid_days(run)),
                 samples=samples, runs=history, detection=detection)


def summary_watermark(run: dict | None) -> str:
    return ((run or {}).get("summary", {}) or {}).get("watermark", "")


@pages.get("/anomalies", response_class=HTMLResponse)
def page_anomalies(request: Request):
    app_state = state(request)
    run = app_state.current_run()
    if run is None:
        return _page(request, "anomalies.html", "anomalies", axis_kind="date",
                     axis=axis_for_dates([], ""), detection={}, timeline={},
                     detections=[], notes=[])
    points = attrib_service.load_view(app_state.store, run["run_id"])
    result = detect_service.detect(points, app_state.settings)
    confirmed = [item for item in result["detections"]
                 if item["status"] in ("confirmed", "candidate")]
    timeline = detect_service.timeline(result["detections"],
                                       run["summary"].get("watermark", ""))
    marks = [{"biz_date": row["biz_date"],
              "status": "confirmed" if any(item["status"] == "confirmed"
                                           for item in row["items"]) else "candidate",
              "count": len(row["items"])} for row in timeline["marks"]]
    return _page(request, "anomalies.html", "anomalies", axis_kind="date",
                 axis=axis_for_dates(timeline["days"], timeline["watermark"], marks),
                 detection=result["summary"], events=result["events"],
                 detections=confirmed[-60:], notes=result["notes"],
                 timeline=timeline)


@pages.get("/review", response_class=HTMLResponse)
def page_review(request: Request, run_id: str = Query(default="")):
    app_state = state(request)
    result = review_service.review(app_state.settings, app_state.store, run_id or None)
    if not result:
        return _page(request, "review.html", "review", axis_kind="date",
                     axis=axis_for_dates([], ""), review={})
    active_run = result.get("run") or app_state.current_run()
    return _page(request, "review.html", "review", axis_kind="date",
                 axis=axis_for_dates(grid_days(active_run),
                                     summary_watermark(active_run),
                                     result.get("marks", [])),
                 review=result, run=active_run)


@pages.get("/attribution", response_class=HTMLResponse)
def page_attribution(request: Request):
    app_state = state(request)
    result = attrib_service.attribution(app_state.settings, app_state.store, None, "hotel_code")
    return _page(request, "attribution.html", "attribution", axis_kind="date",
                 axis=_axis_of(result), attribution=result)


@pages.get("/drilldown", response_class=HTMLResponse)
def page_drilldown(request: Request):
    app_state = state(request)
    by_channel = attrib_service.attribution(app_state.settings, app_state.store, None, "channel")
    by_hotel = attrib_service.attribution(app_state.settings, app_state.store, None, "hotel_code")
    return _page(request, "drilldown.html", "drilldown", axis_kind="date",
                 axis=_axis_of(by_hotel), by_channel=by_channel, by_hotel=by_hotel)


@pages.get("/batches", response_class=HTMLResponse)
def page_batches(request: Request):
    app_state = state(request)
    runs = app_state.store.list_runs(limit=20)
    run = app_state.current_run()
    reconcile = {}
    if run is not None:
        reconcile = attrib_service.reconcile(app_state.settings, app_state.store, run["run_id"])
    history = batches.history(runs, 20)
    return _page(request, "batches.html", "batches", axis_kind="run",
                 axis=axis_for_runs(history, summary_watermark(run), grid_days(run)),
                 runs=history, reconcile=reconcile,
                 account_notes=quality_service.account_checks(
                     (run or {}).get("summary", {}).get("accounts", {})))


@pages.get("/report", response_class=HTMLResponse)
def page_report(request: Request):
    app_state = state(request)
    run = app_state.current_run()
    if run is None:
        return _page(request, "report.html", "report", axis_kind="date",
                     axis=axis_for_dates([], ""), report={})
    report = export_service.report(app_state.settings, app_state.store, run["run_id"])
    return _page(request, "report.html", "report", axis_kind="date",
                 axis=_axis_of(report.get("attribution", {})), report=report,
                 contract=contract.payload())


def _axis_of(result: dict) -> dict:
    base = (result or {}).get("base") or []
    cur = (result or {}).get("cur") or []
    if not base or not cur:
        return axis_for_dates([], "")
    days = axis_days(base[0], cur[1])
    return axis_for_dates(days, (result or {}).get("watermark", ""))
