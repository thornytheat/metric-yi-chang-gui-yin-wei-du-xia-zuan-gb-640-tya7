"""按「门店 × 渠道」构建日序列，并给出序列内的缺失日期。"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal


def build_series(points: list[dict]) -> dict[tuple, list[dict]]:
    series: dict[tuple, list[dict]] = {}
    for point in points:
        key = (point["hotel_code"], point["channel"])
        series.setdefault(key, []).append(point)
    for key in series:
        series[key].sort(key=lambda item: item["biz_date"])
    return series


def series_label(key: tuple) -> str:
    return f"{key[0]}/{key[1]}"


def missing_dates(rows: list[dict], start: dt.date, end: dt.date) -> list[str]:
    """返回 [start, end) 内没有有效点的日期。"""
    have = {dt.date.fromisoformat(row["biz_date"]) for row in rows}
    out: list[str] = []
    day = start
    while day < end:
        if day not in have:
            out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


def to_decimal(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))
