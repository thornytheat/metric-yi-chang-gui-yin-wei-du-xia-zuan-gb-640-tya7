"""M4 异常检测：稳健 z、周末分层、半开窗口、连续确认、MAD 为 0 的退路。"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app import config
from app.modules.m4_detect import madz, series, stratify
from app.modules.m4_detect import service as detect_service
from tests.fixtures import expected as fx

SETTINGS = config.Settings(db_path="/tmp/unused.sqlite", seed_on_start=False)


def _points(rows):
    return [{"hotel_code": "H01", "channel": "ota", "biz_date": day, "stratum": stratum,
             "revenue_net": Decimal(revenue)} for day, stratum, revenue in rows]


def test_confirmed_pair_detected_on_sample(detection):
    confirmed = {(item["hotel_code"], item["channel"], item["biz_date"])
                 for item in detection["detections"] if item["status"] == "confirmed"}
    for key in fx.CONFIRMED:
        assert key in confirmed
    assert detection["summary"]["events"] == 2
    assert detection["summary"]["confirmed_points"] == 4


def test_single_day_spike_is_candidate_only(detection):
    item = next(entry for entry in detection["detections"]
                if (entry["hotel_code"], entry["channel"], entry["biz_date"])
                == fx.CANDIDATE_ONLY)
    assert item["status"] == "candidate"
    assert item["direction"] == "high"
    assert Decimal(item["z"]) > fx.Z_THRESHOLD


def test_insufficient_baseline_not_judged(detection):
    # 每序列前 28 个点没有足够同层历史，一律不判
    assert detection["summary"]["insufficient_points"] == fx.GRID_COMBOS * fx.WINDOW_POINTS
    early = [item for item in detection["detections"]
             if item["status"] == "insufficient"][0]
    assert early["z"] is None
    assert early["baseline_points"] < fx.MIN_BASELINE


def test_mad_zero_falls_back_to_meanad():
    values = [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("130")]
    center = madz.median(values)
    spread, method = madz.scale_of(values, center)
    assert method == "meanad" and spread > 0
    z = madz.robust_z(Decimal("130"), center, spread)
    assert z is not None and z > 0


def test_zero_dispersion_returns_none():
    values = [Decimal("90")] * 6
    center = madz.median(values)
    spread, method = madz.scale_of(values, center)
    assert (spread, method) == (Decimal("0"), "zero")
    assert madz.robust_z(Decimal("90"), center, spread) is None


def test_robust_z_matches_formula():
    values = [Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100")]
    center = madz.median(values)
    spread = madz.mad(values, center)
    z = madz.robust_z(Decimal("112"), center, spread)
    assert center == Decimal("100")
    assert spread == Decimal("0.5")
    assert z == Decimal("16.1880")


def test_window_capped_at_28_same_stratum_points():
    rows = []
    for index in range(40):
        day = dt.date(2026, 9, 1) + dt.timedelta(days=index)
        stratum = "restday" if day.weekday() >= 5 else "workday"
        rows.append({"biz_date": day.isoformat(), "stratum": stratum,
                     "revenue_net": str(1000 + index)})
    window = stratify._window(rows, 39, "workday", SETTINGS)
    assert len(window) <= SETTINGS.window_points
    assert all(item["stratum"] == "workday" for item in window)


def test_half_open_window_excludes_current_day():
    # TRAP-23：窗口是 [当日-28, 当日)，当日不参与自己的基线
    rows = [{"biz_date": (dt.date(2026, 9, 1) + dt.timedelta(days=index)).isoformat(),
             "stratum": "workday", "revenue_net": "1000"} for index in range(30)]
    window = stratify._window(rows, 20, "workday", SETTINGS)
    assert rows[20] not in window
    assert window[-1] is rows[19]


def test_series_built_and_sorted():
    rows = _points([("2026-11-03", "workday", "1200"), ("2026-11-01", "restday", "1500")])
    grouped = series.build_series(rows)
    assert list(grouped) == [("H01", "ota")]
    assert [item["biz_date"] for item in grouped[("H01", "ota")]] == ["2026-11-01", "2026-11-03"]
    assert series.series_label(("H01", "ota")) == "H01/ota"


def test_missing_dates_half_open():
    rows = [{"biz_date": "2026-11-01"}, {"biz_date": "2026-11-03"}]
    missing = series.missing_dates(rows, dt.date(2026, 11, 1), dt.date(2026, 11, 4))
    assert missing == ["2026-11-02"]


def test_timeline_marks_and_watermark(detection):
    timeline = detect_service.timeline(detection["detections"], fx.WATERMARK)
    assert timeline["watermark"] == fx.WATERMARK
    dates = {row["biz_date"] for row in timeline["marks"]}
    assert "2026-11-17" in dates and "2026-11-19" in dates
    confirmed_days = [row for row in timeline["marks"] if row["biz_date"] == "2026-11-17"]
    assert confirmed_days[0]["items"][0]["status"] == "confirmed"


def test_filter_detections_by_status(detection):
    confirmed = detect_service.filter_detections(detection["detections"], "confirmed")
    assert len(confirmed) == 4
    channels = detect_service.filter_detections(detection["detections"], "", "direct")
    assert {item["channel"] for item in channels} == {"direct"}


def test_baseline_minimum_boundary_thirteen_points():
    # 同层点数 13 不判、14 才判：边界既不提前判也不漏判
    rows = [{"biz_date": (dt.date(2026, 9, 1) + dt.timedelta(days=index)).isoformat(),
             "stratum": "workday", "revenue_net": str(1000 + (index % 5) * 10)}
            for index in range(16)]
    scan = stratify.scan_series(("H01", "ota"), rows, SETTINGS)
    statuses = [item.status for item in scan.detections]
    assert statuses[13] == "insufficient"
    assert statuses[13 - 1] == "insufficient"
    assert statuses[14] != "insufficient"
    assert scan.detections[14].baseline_points == SETTINGS.min_baseline_points
