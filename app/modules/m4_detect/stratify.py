"""稳健 z 异常检测：同层滚动窗口、候选与连续确认。

规则：窗口取当日之前最近 28 个同层点（左闭右开，不含当日）；
|z| ≥ 3.5 为候选；相邻业务日同向连续 2 天为确认；同层点不足 14 不判。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from app import config, reasons
from app.modules.m4_detect import madz, series


@dataclass
class Detection:
    hotel_code: str
    channel: str
    biz_date: str
    stratum: str
    value: Decimal
    median: Decimal | None = None
    dispersion: Decimal | None = None
    z: Decimal | None = None
    method: str = "mad"
    status: str = "normal"          # normal / candidate / confirmed / insufficient / undefined
    direction: str = ""
    baseline_points: int = 0
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "hotel_code": self.hotel_code,
            "channel": self.channel,
            "series": series.series_label((self.hotel_code, self.channel)),
            "biz_date": self.biz_date,
            "stratum": self.stratum,
            "value": str(self.value),
            "median": None if self.median is None else str(self.median),
            "dispersion": None if self.dispersion is None else str(self.dispersion),
            "z": None if self.z is None else str(self.z),
            "method": self.method,
            "status": self.status,
            "direction": self.direction,
            "baseline_points": self.baseline_points,
            "note": self.note,
        }


@dataclass
class SeriesScan:
    key: tuple
    detections: list[Detection] = field(default_factory=list)
    confirmed: int = 0
    candidate_only: int = 0
    skipped: int = 0


def _window(rows: list[dict], index: int, stratum: str, settings: config.Settings) -> list[dict]:
    history = [row for row in rows[:index] if row["stratum"] == stratum]
    return history[-settings.window_points:]


def scan_series(key: tuple, rows: list[dict], settings: config.Settings) -> SeriesScan:
    scan = SeriesScan(key=key)
    for index, row in enumerate(rows):
        value = series.to_decimal(row["revenue_net"])
        window = _window(rows, index, row["stratum"], settings)
        detection = Detection(hotel_code=key[0], channel=key[1], biz_date=row["biz_date"],
                              stratum=row["stratum"], value=value,
                              baseline_points=len(window))
        if len(window) < settings.min_baseline_points:
            detection.status = "insufficient"
            detection.note = "同层历史点不足 %d" % settings.min_baseline_points
            scan.skipped += 1
            scan.detections.append(detection)
            continue
        values = [series.to_decimal(item["revenue_net"]) for item in window]
        center = madz.median(values)
        dispersion, method = madz.scale_of(values, center)
        z = madz.robust_z(value, center, dispersion)
        detection.median = center.quantize(Decimal("0.01"))
        detection.dispersion = dispersion.quantize(Decimal("0.0001"))
        detection.method = method
        detection.z = z
        if method == "zero" or z is None:
            detection.status = "undefined"
            detection.note = "平均绝对偏差为 0，本点不判"
            scan.skipped += 1
        elif abs(z) >= settings.z_threshold:
            detection.status = "candidate"
            detection.direction = "high" if z > 0 else "low"
        else:
            detection.status = "normal"
        scan.detections.append(detection)
    _confirm(scan, settings)
    return scan


def _confirm(scan: SeriesScan, settings: config.Settings) -> None:
    by_date = {item.biz_date: item for item in scan.detections}
    for detection in scan.detections:
        if detection.status != "candidate":
            continue
        day = dt.date.fromisoformat(detection.biz_date)
        previous = by_date.get((day - dt.timedelta(days=1)).isoformat())
        if previous and previous.status in ("candidate", "confirmed") \
                and previous.direction == detection.direction:
            detection.status = "confirmed"
            detection.note = "与前一日同向连续"
            if previous.status == "candidate":
                previous.status = "confirmed"
                previous.note = "与后一日同向连续"
    scan.confirmed = sum(1 for item in scan.detections if item.status == "confirmed")
    scan.candidate_only = sum(1 for item in scan.detections if item.status == "candidate")


def method_note(scan: SeriesScan) -> list[dict]:
    notes: dict[str, int] = {}
    for detection in scan.detections:
        if detection.method in ("meanad", "zero"):
            notes[detection.method] = notes.get(detection.method, 0) + 1
    out = []
    if notes.get("meanad"):
        out.append({"code": "N_NO_MAD", "label": reasons.label("N_NO_MAD"),
                    "detail": f"MAD 为 0，改用平均绝对偏差 {notes['meanad']} 个点"})
    if notes.get("zero"):
        out.append({"code": "N_Z_UNDEFINED", "label": reasons.label("N_Z_UNDEFINED"),
                    "detail": f"离散度为 0，未判定 {notes['zero']} 个点"})
    return out
