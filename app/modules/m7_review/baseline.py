"""复盘基线：把节假日从同层窗口里单列出来，再看离散度是否退化。

检测用的同层窗口把节假日并入休息日；长假的门店量级明显高于普通周末，
混着长假算基线会把普通周末判成偏低。复盘按固定顺序重算一遍：

1. 取当日之前最近 N 个同层点，左闭右开，不含当日；
2. 剔除其中的节假日点，剩下的点做基线；剔空了才退回全量；
3. 中位数与离散度照常算，MAD 为 0 退到平均绝对偏差，两者都为 0 时
   不做除法、不猜结论，直接返回退化码 R_NO_DISPERSION。

本文件只做算术，不读库、不装配报告。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app import config, reasons
from app.modules.m4_detect import madz, series

ZERO_CODE = reasons.REVIEW_NO_DISPERSION
HOLIDAY_CODE = reasons.REVIEW_HOLIDAY_THIN
# 节假日占基线三成以上，就认为这一层的基线不足以代表普通周末
HOLIDAY_SHARE_LIMIT = Decimal("0.3")
MONEY_QUANT = Decimal("0.01")
SPREAD_QUANT = Decimal("0.0001")


def is_holiday(day: dt.date, holidays: set[dt.date] | None = None) -> bool:
    return day in (config.HOLIDAYS if holidays is None else holidays)


def stratum_of(day: dt.date, holidays: set[dt.date] | None = None) -> str:
    """复盘分三层：工作日、休息日、节假日。节假日不并入休息日。"""
    if is_holiday(day, holidays):
        return "holiday"
    return "restday" if day.weekday() >= 5 else "workday"


def review_window(rows: list[dict], index: int, stratum: str,
                  window_points: int) -> list[dict]:
    """当日之前最近 N 个同层点；当日不参与自己的基线。"""
    history = [row for row in rows[:index] if row["stratum"] == stratum]
    return history[-window_points:] if window_points > 0 else []


def review_baseline(rows: list[dict], index: int, window_points: int) -> dict:
    """剔除节假日后重算中位数与离散度；退化情况用 method 标出来。"""
    row = rows[index]
    window = review_window(rows, index, row["stratum"], window_points)
    holiday_dates = {item["biz_date"] for item in window
                     if is_holiday(dt.date.fromisoformat(item["biz_date"]))}
    clean = [item for item in window if item["biz_date"] not in holiday_dates]
    active = clean or window
    values = [series.to_decimal(item["revenue_net"]) for item in active]
    if not values:
        return {
            "points": 0, "used_points": 0, "holiday_points": 0,
            "holiday_share": Decimal("0"), "stratum": row["stratum"],
            "median": Decimal("0"), "mad": Decimal("0"), "meanad": Decimal("0"),
            "dispersion": Decimal("0"), "method": "zero",
        }
    center = madz.median(values)
    spread, method = madz.scale_of(values, center)
    share = (Decimal(len(holiday_dates)) / Decimal(len(window))) if window else Decimal("0")
    return {
        "points": len(window),
        "used_points": len(active),
        "holiday_points": len(holiday_dates),
        "holiday_share": share.quantize(SPREAD_QUANT),
        "stratum": row["stratum"],
        "median": center,
        "mad": madz.mad(values, center),
        "meanad": madz.mean_abs_dev(values, center),
        "dispersion": spread,
        "method": method,
    }


def review_z(value, baseline: dict) -> tuple[Decimal | None, str]:
    """基线离散度为 0 时返回 (None, ZERO_CODE)：这里没有除法。"""
    if baseline["method"] == "zero" or Decimal(baseline["dispersion"]) == 0:
        return None, ZERO_CODE
    return madz.robust_z(Decimal(value), baseline["median"], baseline["dispersion"]), ""


def holiday_flag(baseline: dict, value, threshold: Decimal) -> dict | None:
    """节假日占三成以上即分层不足；剔除后仍站不住时降级为低置信。"""
    if baseline["points"] == 0 or baseline["holiday_share"] < HOLIDAY_SHARE_LIMIT:
        return None
    z, _ = review_z(value, baseline)
    percent = (baseline["holiday_share"] * 100).quantize(Decimal("0.1"))
    head = f"节假日 {baseline['holiday_points']} 点占基线 {percent}%"
    if z is None:
        return {"code": HOLIDAY_CODE, "detail": head + "，剔除后不判", "downgrade": True}
    if abs(z) < threshold:
        return {"code": HOLIDAY_CODE, "detail": head + "，剔除后 |z| 不足阈值",
                "downgrade": True}
    return {"code": HOLIDAY_CODE, "detail": head, "downgrade": False}


def as_payload(baseline: dict, value, threshold: Decimal) -> dict:
    z, code = review_z(value, baseline)
    return {
        "points": baseline["points"],
        "used_points": baseline["used_points"],
        "holiday_points": baseline["holiday_points"],
        "holiday_share": str(baseline["holiday_share"]),
        "stratum": baseline["stratum"],
        "median": str(baseline["median"].quantize(MONEY_QUANT)),
        "mad": str(baseline["mad"].quantize(SPREAD_QUANT)),
        "meanad": str(baseline["meanad"].quantize(SPREAD_QUANT)),
        "dispersion": str(baseline["dispersion"].quantize(SPREAD_QUANT)),
        "method": baseline["method"],
        "z": None if z is None else str(z),
        "code": code,
        "threshold": str(threshold),
    }
