"""异常检测编排：整批序列扫描、汇总、按状态筛选。

检测结果不落库：同一份指标点与同一套规则参数必然复算出同样的结论，
批次页只存参数与输入摘要，结论随参数现算。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app import config
from app.modules.m4_detect import series, stratify


def _episodes(detections: list[dict]) -> list[dict]:
    """连续确认点合并成一次异常事件，页面与报告按事件计数。"""
    by_series: dict[tuple, list[dict]] = {}
    for item in detections:
        if item["status"] != "confirmed":
            continue
        by_series.setdefault((item["hotel_code"], item["channel"]), []).append(item)
    events: list[dict] = []
    for key in sorted(by_series):
        items = sorted(by_series[key], key=lambda entry: entry["biz_date"])
        run: list[dict] = []
        for item in items:
            if run:
                last_day = dt.date.fromisoformat(run[-1]["biz_date"])
                if dt.date.fromisoformat(item["biz_date"]) - last_day == dt.timedelta(days=1):
                    run.append(item)
                    continue
                events.append(_event(key, run))
            run = [item]
        if run:
            events.append(_event(key, run))
    events.sort(key=lambda entry: (-abs(Decimal(entry["delta"])), entry["start"]))
    return events


def _event(key: tuple, run: list[dict]) -> dict:
    first, last = run[0], run[-1]
    delta = Decimal(last["value"]) - Decimal(first["median"] or last["value"])
    return {
        "series": series.series_label(key),
        "hotel_code": key[0],
        "channel": key[1],
        "start": first["biz_date"],
        "end": last["biz_date"],
        "days": len(run),
        "direction": first["direction"],
        "delta": str(delta.quantize(Decimal("0.01"))),
        "peak_z": str(max(run, key=lambda item: abs(Decimal(item["z"] or "0")))["z"]),
        "stratum": first["stratum"],
    }


def detect(points: list[dict], settings: config.Settings) -> dict:
    scans = []
    all_detections: list[dict] = []
    notes: list[dict] = []
    grouped = series.build_series(points)
    for key in sorted(grouped):
        rows = grouped[key]
        scan = stratify.scan_series(key, rows, settings)
        scans.append(scan)
        all_detections.extend(item.as_dict() for item in scan.detections)
        for note in stratify.method_note(scan):
            if note not in notes:
                notes.append(note)
    events = _episodes(all_detections)
    summary = {
        "series": len(scans),
        "points": len(points),
        "confirmed_points": sum(1 for item in all_detections if item["status"] == "confirmed"),
        "candidate_points": sum(1 for item in all_detections if item["status"] == "candidate"),
        "insufficient_points": sum(1 for item in all_detections
                                   if item["status"] == "insufficient"),
        "undefined_points": sum(1 for item in all_detections if item["status"] == "undefined"),
        "events": len(events),
        "threshold": str(settings.z_threshold),
        "window_points": settings.window_points,
        "min_baseline": settings.min_baseline_points,
    }
    return {"summary": summary, "events": events, "detections": all_detections,
            "notes": notes}


def filter_detections(detections: list[dict], status: str = "", channel: str = "",
                      hotel: str = "") -> list[dict]:
    out = []
    for item in detections:
        if status and item["status"] != status:
            continue
        if channel and item["channel"] != channel:
            continue
        if hotel and item["hotel_code"] != hotel:
            continue
        out.append(item)
    return out


def timeline(detections: list[dict], watermark: str) -> dict:
    days = sorted({item["biz_date"] for item in detections})
    marks: dict[str, list[dict]] = {}
    for item in detections:
        if item["status"] in ("candidate", "confirmed"):
            marks.setdefault(item["biz_date"], []).append({
                "series": item["series"], "status": item["status"], "z": item["z"],
                "direction": item["direction"],
            })
    return {
        "days": days,
        "watermark": watermark,
        "marks": [{"biz_date": day, "items": marks[day]} for day in days if day in marks],
    }
