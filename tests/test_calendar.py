"""M2 日历与归一：千分位/全角/单位、哨兵三态、时区、分层、半开区间。"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

import pytest

from app import config
from app.modules.m2_calendar import calendar_rules, contract, service, tz

SETTINGS = config.Settings(db_path="/tmp/unused.sqlite", seed_on_start=False)


def _row(**overrides) -> dict:
    base = {
        "hotel_code": "H01", "hotel_name": "蓝湾·滨海", "channel": "ota",
        "biz_date": "2026-11-03", "rooms_available": "180", "rooms_sold": "120",
        "adr": "700.00", "revenue_net": "84000.00", "updated_at": "2026-11-04T06:00:00+08:00",
        "_line_no": 7,
    }
    base.update(overrides)
    return base


def test_thousands_and_fullwidth_numbers_normalized():
    # TRAP-06：千分位与全角数字都要归一成一个十进制值
    assert service.to_decimal("1,234.56") == ("ok", Decimal("1234.56"))
    assert service.to_decimal("１，２３４．５") == ("ok", Decimal("1234.5"))
    assert service.to_decimal("１２３４.５０") == ("ok", Decimal("1234.50"))


def test_unit_suffix_stripped():
    # TRAP-06：单位后缀不能留成字符串
    assert service.to_decimal("980元/间夜") == ("ok", Decimal("980"))
    assert service.to_decimal("￥980") == ("ok", Decimal("980"))


def test_naive_float_parse_would_raise():
    # TRAP-06 反例：朴素 Decimal/float 直接吃原串会抛错
    with pytest.raises(InvalidOperation):
        Decimal("1,234.56")
    status, value = service.to_decimal("1,234.56")
    assert (status, value) == ("ok", Decimal("1234.56"))


def test_sentinel_is_three_state_not_zero():
    # TRAP-07：哨兵与空串、null 三态分开，绝不落成 0
    for text in ("N/A", "-", "未知", "", "待补"):
        status, value = service.to_decimal(text)
        assert status == "sentinel"
        assert value is None
    row, rejects = service.coerce_row(_row(adr="N/A"), SETTINGS)
    assert row is None
    assert [item["code"] for item in rejects] == ["Q_SENTINEL"]


def test_timezone_offsets_collapse_to_same_business_day():
    # TRAP-11：同一时刻的两种写法必须落进同一业务日
    utc = tz.parse_instant("2026-11-03T22:00:00Z")
    offset = tz.parse_instant("2026-11-04T06:00:00+08:00")
    assert utc == offset
    assert tz.local_date(utc) == dt.date(2026, 11, 4)
    assert tz.local_date(offset) == dt.date(2026, 11, 4)


def test_naive_instant_uses_business_timezone():
    naive = tz.parse_instant("2026-11-04T06:00:00")
    assert tz.utc_text(naive) == "2026-11-03T22:00:00Z"


def test_stratum_separates_holiday_and_weekend():
    workday = calendar_rules.day_stratum(dt.date(2026, 11, 3))
    weekend = calendar_rules.day_stratum(dt.date(2026, 11, 7))
    holiday = calendar_rules.day_stratum(dt.date(2026, 10, 5))
    assert workday == "workday"
    assert weekend == "restday"
    assert holiday == "restday"


def test_half_open_span_excludes_end():
    # TRAP-23：区间左闭右开，相邻期间不重不漏
    days = calendar_rules.span(dt.date(2026, 11, 1), 3)
    assert [day.isoformat() for day in days] == ["2026-11-01", "2026-11-02", "2026-11-03"]
    left = set(calendar_rules.span(dt.date(2026, 11, 1), 4))
    right = set(calendar_rules.span(dt.date(2026, 11, 5), 4))
    assert not left & right
    assert calendar_rules.span(dt.date(2026, 11, 1), 0) == []


def test_lateness_boundaries():
    # TRAP-13 前置：迟到天数与窗口边界
    assert calendar_rules.is_late(1, 1) is False
    assert calendar_rules.is_late(2, 1) is True
    assert calendar_rules.is_out_of_window(3, 3) is False
    assert calendar_rules.is_out_of_window(4, 3) is True


def test_merge_periods_flags_overlap():
    merged = calendar_rules.merge_periods(dt.date(2026, 11, 8), 7, dt.date(2026, 11, 15), 7)
    assert merged["overlap"] is False
    assert merged["touch"] is True
    overlap = calendar_rules.merge_periods(dt.date(2026, 11, 8), 10, dt.date(2026, 11, 15), 7)
    assert overlap["overlap"] is True


def test_contract_payload_has_fields_and_rules():
    payload = contract.payload()
    names = [item["name"] for item in payload["fields"]]
    assert names[:3] == ["hotel_code", "hotel_name", "channel"]
    assert {"stratum", "window", "zscore", "confirm"} <= {item["key"] for item in payload["rules"]}
    assert payload["grid"]["key"] == ["hotel_code", "channel", "biz_date"]


def test_coerce_row_derives_stratum_and_lateness():
    row, rejects = service.coerce_row(_row(updated_at="2026-11-07T06:00:00+08:00"), SETTINGS)
    assert rejects == []
    assert row.stratum == "workday"
    assert row.late_days == 4
    assert row.snapshot_local == dt.date(2026, 11, 7)


def test_future_business_day_rejected():
    row, rejects = service.coerce_row(_row(biz_date="2026-12-01"), SETTINGS)
    assert row is None
    assert rejects[0]["code"] == "Q_FUTURE_DATE"


def test_value_sample_file_coerces_cleanly():
    from pathlib import Path
    from app.modules.m1_ingest import service as ingest_service

    root = Path(__file__).resolve().parent.parent
    data = (root / "sample_data" / "daily_metrics_values.csv").read_bytes()
    result = ingest_service.ingest(data, "values.csv", SETTINGS)
    coerced, rejects = service.coerce_rows(result.rows, SETTINGS)
    assert len(rejects) == 1 and rejects[0]["code"] == "Q_SENTINEL"
    ota = [row for row in coerced if row.channel == "ota"]
    assert ota[0].adr == Decimal("1234.56")
    assert ota[1].adr == Decimal("1234.50")
    assert ota[2].adr == Decimal("980.00")
    member = [row for row in coerced if row.channel == "member"]
    assert member[0].adr == Decimal("1050.00")
