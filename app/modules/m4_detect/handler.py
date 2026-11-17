"""异常检测入口：检测结果、事件清单、时间轴刻度。"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app import errors
from app.modules.m4_detect import service
from app.web import state

router = APIRouter()


def _detect(request: Request) -> tuple[dict, dict]:
    app_state = state(request)
    run = app_state.current_run()
    if run is None:
        raise errors.NotFound("还没有运行记录", code="E_NO_RUN")
    points = app_state.store.load_points(run["run_id"])
    if not points:
        raise errors.BadInput("当前批次没有有效指标点", code="E_NO_POINTS")
    return run, service.detect(points, app_state.settings)


@router.get("/api/anomalies")
def get_anomalies(
    request: Request,
    status: str = Query(default=""),
    channel: str = Query(default=""),
    hotel: str = Query(default=""),
):
    run, result = _detect(request)
    detection_rows = service.filter_detections(result["detections"], status, channel, hotel)
    flagged = [item for item in detection_rows
               if item["status"] in ("confirmed", "candidate")]
    others = [item for item in detection_rows if item["status"] not in ("confirmed", "candidate")]
    return {
        "run_id": run["run_id"],
        "watermark": run["summary"].get("watermark", ""),
        "summary": result["summary"],
        "notes": result["notes"],
        "events": result["events"],
        "flagged_total": len(flagged),
        "detections": (flagged + others)[:200],
        "timeline": service.timeline(detection_rows, run["summary"].get("watermark", "")),
    }


@router.get("/api/anomalies/timeline")
def get_timeline(request: Request):
    run, result = _detect(request)
    return {
        "run_id": run["run_id"],
        "summary": result["summary"],
        "timeline": service.timeline(result["detections"],
                                     run["summary"].get("watermark", "")),
    }
