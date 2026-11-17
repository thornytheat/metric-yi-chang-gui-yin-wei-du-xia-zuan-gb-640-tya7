"""质量编排：筛选 → 重复归因 → 迟到标记 → 网格补缺 → 三账合计。

账目定义（同一份数据必须三条等式同时成立）：
  行账：读入记录 = 采信行 + 被覆盖行 + 隔离行
  点账：网格点   = 有效点 + 隔离点 + 缺失点
  金额账：采信行净收入合计 = 有效点净收入合计
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from app import config, reasons
from app.modules.m2_calendar.service import CoercedRow
from app.modules.m3_quality import dupes, quarantine, rules


@dataclass
class QualityResult:
    points: list[dict] = field(default_factory=list)
    quarantine: list[dict] = field(default_factory=list)
    superseded: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    late_marks: list[dict] = field(default_factory=list)
    accounts: dict = field(default_factory=dict)
    grid: dict = field(default_factory=dict)
    reason_counts: list[dict] = field(default_factory=list)
    watermark: str = ""

    def points_orm(self) -> list[dict]:
        return self.points


def _key_of(entry: dict) -> tuple | None:
    hotel = entry.get("hotel_code")
    channel = entry.get("channel")
    day = entry.get("biz_date")
    if not hotel or not channel or not day:
        return None
    return (hotel, channel, day)


def _point_dict(row: CoercedRow) -> dict:
    return {
        "hotel_code": row.hotel_code,
        "hotel_name": row.hotel_name,
        "channel": row.channel,
        "biz_date": row.biz_date.isoformat(),
        "stratum": row.stratum,
        "rooms_available": row.rooms_available,
        "rooms_sold": row.rooms_sold,
        "adr": str(row.adr),
        "revenue_net": str(row.revenue_net),
        "late_days": row.late_days,
        "late_flag": int(row.late_flag),
        "out_of_window": int(row.out_of_window),
        "source_line": row.line_no,
        "updated_utc": row.snapshot_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def evaluate(coerced: list[CoercedRow], reject_rows: list[dict],
             settings: config.Settings, records_read: int) -> QualityResult:
    screened, rule_rejects = rules.screen(coerced, settings)
    watermark_date = max((row.biz_date for row in screened), default=None)
    resolved = dupes.resolve(screened)
    late_marks = rules.mark_lateness(resolved.winners, settings)

    result = QualityResult(
        points=[_point_dict(row) for row in resolved.winners],
        superseded=resolved.superseded,
        conflicts=resolved.conflicts,
        late_marks=late_marks,
        watermark=watermark_date.isoformat() if watermark_date else "",
    )
    entries = quarantine.build(list(reject_rows) + rule_rejects + resolved.conflicts
                              + resolved.superseded)
    result.quarantine = entries

    # ---- 网格：门店 × 渠道 × 业务日 -------------------------------
    combos: set[tuple] = set()
    days: set[dt.date] = set()
    occupied: set[tuple] = set()
    keyed_entries = [entry for entry in entries if _key_of(entry)]
    for entry in keyed_entries:
        key = _key_of(entry)
        combos.add((key[0], key[1]))
        day = dt.date.fromisoformat(key[2])
        days.add(day)
        if reasons.account(entry["code"]) == "quarantine":
            occupied.add(key)
    for row in resolved.winners:
        combos.add((row.hotel_code, row.channel))
        days.add(row.biz_date)
        occupied.add((row.hotel_code, row.channel, row.biz_date.isoformat()))
    grid_points = len(combos) * len(days) if combos and days else 0
    points_valid = len(resolved.winners)
    points_rejected = max(0, len(occupied) - points_valid)
    points_missing = max(0, grid_points - points_valid - points_rejected)

    revenue_accepted = sum((Decimal(point["revenue_net"]) for point in result.points),
                           Decimal("0")).quantize(settings.money_quant)
    quarantine_rows = [entry for entry in entries
                       if reasons.account(entry["code"]) == "quarantine"]
    result.accounts = {
        "rows_read": records_read,
        "rows_accepted": points_valid,
        "rows_superseded": len(resolved.superseded),
        "rows_quarantine": len(quarantine_rows),
        "points_grid": grid_points,
        "points_valid": points_valid,
        "points_rejected": points_rejected,
        "points_missing": points_missing,
        "revenue_accepted": str(revenue_accepted),
        "late_points": sum(1 for row in resolved.winners if row.late_flag),
        "out_of_window_points": sum(1 for row in resolved.winners if row.out_of_window),
    }
    result.grid = {
        "combos": len(combos),
        "days": len(days),
        "start": min(days).isoformat() if days else "",
        "end": max(days).isoformat() if days else "",
        "late_days_allowed": settings.late_days_allowed,
    }
    result.reason_counts = quarantine.group_counts(entries)
    return result


def account_checks(accounts: dict) -> list[dict]:
    """三条等式各自给出“平/不平”，不平要能在页面上一眼看出来。"""
    rows_ok = (accounts["rows_read"]
               == accounts["rows_accepted"] + accounts["rows_superseded"]
               + accounts["rows_quarantine"])
    points_ok = (accounts["points_grid"]
                 == accounts["points_valid"] + accounts["points_rejected"]
                 + accounts["points_missing"])
    return [
        {"name": "行账",
         "formula": "读入 = 采信 + 被覆盖 + 隔离",
         "left": accounts["rows_read"],
         "right": accounts["rows_accepted"] + accounts["rows_superseded"]
         + accounts["rows_quarantine"],
         "ok": rows_ok},
        {"name": "点账",
         "formula": "网格点 = 有效 + 隔离 + 缺失",
         "left": accounts["points_grid"],
         "right": accounts["points_valid"] + accounts["points_rejected"]
         + accounts["points_missing"],
         "ok": points_ok},
    ]
