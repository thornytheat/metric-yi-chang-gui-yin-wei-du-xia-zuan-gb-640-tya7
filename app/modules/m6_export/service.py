"""导出编排：报告结构、CSV 与清单文件。"""
from __future__ import annotations

from app import errors
from app.modules.m4_detect import service as detect_service
from app.modules.m5_attrib import service as attrib_service
from app.modules.m6_export import csv_out, manifest_out, report_build

KINDS = {
    "points.csv": "指标点",
    "anomalies.csv": "异常点",
    "attribution.csv": "归因表",
    "manifest.json": "运行清单",
}


def _run(store, run_id: str | None) -> dict:
    run = store.get_run(run_id) if run_id else store.latest_run()
    if run is None:
        raise errors.NotFound("还没有运行记录", code="E_NO_RUN")
    return run


def report(settings, store, run_id: str | None = None) -> dict:
    run = _run(store, run_id)
    points = attrib_service.load_view(store, run["run_id"])
    detection = detect_service.detect(points, settings)
    attribution = attrib_service.attribution(settings, store, run["run_id"], "hotel_code", 7, 7)
    reconcile = attrib_service.reconcile(settings, store, run["run_id"])
    return report_build.build(run, detection, attribution, reconcile)


def export_text(settings, store, kind: str, run_id: str | None = None) -> tuple[str, str, str]:
    """返回 (内容, 媒体类型, 文件名)。"""
    if kind not in KINDS:
        raise errors.BadInput("未知导出类型：" + kind, code="E_BAD_EXPORT")
    run = _run(store, run_id)
    if kind == "points.csv":
        points = store.load_points(run["run_id"])
        return csv_out.points_csv(points), "text/csv; charset=utf-8", f"points-{run['run_id']}.csv"
    if kind == "anomalies.csv":
        points = attrib_service.load_view(store, run["run_id"])
        detection = detect_service.detect(points, settings)
        body = csv_out.anomalies_csv(detection["detections"])
        return body, "text/csv; charset=utf-8", f"anomalies-{run['run_id']}.csv"
    if kind == "attribution.csv":
        result = attrib_service.attribution(settings, store, run["run_id"], "hotel_code", 7, 7)
        return (csv_out.attribution_csv(result), "text/csv; charset=utf-8",
                f"attribution-{run['run_id']}.csv")
    return (manifest_out.manifest_json(run), "application/json",
            f"manifest-{run['run_id']}.json")
