"""业务筛选：负值、可售为 0、超售越界、迟到标记。

数值解析已在 M2 完成；这里只判“值本身是否成立”，不成立就给稳定原因码。
"""
from __future__ import annotations

from app import config, reasons
from decimal import Decimal

from app.modules.m2_calendar.service import CoercedRow


def _entry(row: CoercedRow, code: str, field: str, raw: str, detail: str) -> dict:
    return {
        "line_no": row.line_no, "code": code, "field": field, "raw": raw,
        "label": reasons.label(code), "detail": detail,
        "hotel_code": row.hotel_code, "channel": row.channel,
        "biz_date": row.biz_date.isoformat(),
    }


def screen(rows: list[CoercedRow], settings: config.Settings) -> tuple[list[CoercedRow], list[dict]]:
    passed: list[CoercedRow] = []
    rejects: list[dict] = []
    for row in rows:
        if row.rooms_available <= 0:
            rejects.append(_entry(row, "Q_NEGATIVE", "rooms_available",
                                  str(row.rooms_available), "可售房晚必须为正"))
            continue
        if row.rooms_sold < 0:
            rejects.append(_entry(row, "Q_NEGATIVE", "rooms_sold",
                                  str(row.rooms_sold), "已售房晚为负"))
            continue
        if row.adr < 0 or row.revenue_net < 0:
            rejects.append(_entry(row, "Q_NEGATIVE", "revenue_net",
                                  str(row.revenue_net), "房价或净收入为负"))
            continue
        limit = (Decimal(row.rooms_available) * settings.oversell_limit)
        if Decimal(row.rooms_sold) > limit:
            rejects.append(_entry(row, "Q_OVERSELL", "rooms_sold",
                                  f"{row.rooms_sold}/{row.rooms_available}",
                                  "已售超过可售的 110%"))
            continue
        passed.append(row)
    return passed, rejects


def mark_lateness(rows: list[CoercedRow], settings: config.Settings) -> list[dict]:
    """迟到点仍计入，只是打标记；超窗单独记，好让报告区分「补报」与「补报太晚」。"""
    from app.modules.m2_calendar import calendar_rules

    marks: list[dict] = []
    for row in rows:
        late = calendar_rules.is_late(row.late_days, settings.late_days_allowed)
        out_of_window = calendar_rules.is_out_of_window(row.late_days,
                                                        settings.late_window_days)
        row.late_flag = late
        row.out_of_window = out_of_window
        if late:
            marks.append({"code": "L_LATE_ARRIVAL" if not out_of_window else "L_OUT_OF_WINDOW",
                          "line_no": row.line_no,
                          "hotel_code": row.hotel_code, "channel": row.channel,
                          "biz_date": row.biz_date.isoformat(),
                          "late_days": row.late_days,
                          "label": reasons.label("L_LATE_ARRIVAL" if not out_of_window
                                                 else "L_OUT_OF_WINDOW")})
    return marks
