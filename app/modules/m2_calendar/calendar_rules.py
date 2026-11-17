"""日历规则：分层、业务日桶、半开区间与迟到天数。

所有日期区间一律左闭右开 [start, end)，相邻期间不重不漏。
"""
from __future__ import annotations

import datetime as dt

from app import config


def day_stratum(day: dt.date, holidays: set[dt.date] | None = None) -> str:
    """工作日与休息日两分层：周六周日与法定节假日都算休息日。"""
    holiday_set = config.HOLIDAYS if holidays is None else holidays
    if day in holiday_set:
        return "restday"
    return "restday" if day.weekday() >= 5 else "workday"


def span(start: dt.date, days: int) -> list[dt.date]:
    """左闭右开区间内的全部日期。"""
    if days <= 0:
        return []
    return [start + dt.timedelta(days=offset) for offset in range(days)]


def shift(day: dt.date, days: int) -> dt.date:
    return day + dt.timedelta(days=days)


def lateness_days(biz_date: dt.date, snapshot_local: dt.date) -> int:
    return (snapshot_local - biz_date).days


def is_late(lateness: int, allowed: int = config.Settings().late_days_allowed) -> bool:
    return lateness > allowed


def is_out_of_window(lateness: int,
                     window_days: int = config.Settings().late_window_days) -> bool:
    """迟到天数超出补报窗口：仍计入，但标记为补报太晚。"""
    return lateness > window_days


def merge_periods(base_start: dt.date, base_days: int,
                  cur_start: dt.date, cur_days: int) -> dict:
    """基期与当期必须首尾相接或分离，重叠给出显式冲突。"""
    base_end = shift(base_start, base_days)
    cur_end = shift(cur_start, cur_days)
    overlap = base_start < cur_end and cur_start < base_end
    return {
        "base": [base_start.isoformat(), base_end.isoformat()],
        "current": [cur_start.isoformat(), cur_end.isoformat()],
        "overlap": overlap,
        "touch": base_end == cur_start,
    }
