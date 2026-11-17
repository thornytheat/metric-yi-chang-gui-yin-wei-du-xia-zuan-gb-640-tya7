"""M7 异常复盘：基线剔除节假日、MAD 为 0 不除零、同批异常、卡片导出与 4xx。"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, DivisionByZero

import pytest
from fastapi.testclient import TestClient

from app import config, reasons
from app.main import create_app
from app.modules.m4_detect import madz
from app.modules.m5_attrib import service as batch_service
from app.modules.m7_review import baseline, cards_csv, corr, service


def _flat_csv() -> bytes:
    """一条几乎不动的序列：周末 1000、节假日 1010，11-14 / 11-15 连跌。

    节假日只抬 1%，MAD 为 0 后 M4 退到平均绝对偏差仍能判；
    复盘把节假日剔掉后基线全等，只能给退化码。
    """
    rows = ["hotel_code,hotel_name,channel,biz_date,rooms_available,rooms_sold,adr,"
            "revenue_net,updated_at"]
    day = dt.date(2026, 8, 1)
    while day <= dt.date(2026, 11, 15):
        revenue = Decimal("1000.00")
        if day in config.HOLIDAYS:
            revenue = Decimal("1010.00")
        if day.isoformat() == "2026-11-14":
            revenue = Decimal("800.00")
        if day.isoformat() == "2026-11-15":
            revenue = Decimal("790.00")
        stamp = (day + dt.timedelta(days=1)).isoformat()
        rows.append(f"X01,蓝湾·测试,ota,{day.isoformat()},100,80,12.50,{revenue},"
                    f"{stamp}T06:00:00+08:00")
        day += dt.timedelta(days=1)
    return ("\n".join(rows) + "\n").encode("utf-8")


CYCLE = (Decimal("0"), Decimal("20"), Decimal("-20"), Decimal("10"))


def _restday_rows() -> list[dict]:
    """30 个休息日点：普通周末 1000±20，节假日抬到 1600；最后一个窗口含 10 个节假日。"""
    rows = []
    day = dt.date(2026, 8, 1)
    index = 0
    while day <= dt.date(2026, 10, 20):
        if day.weekday() >= 5 or day in config.HOLIDAYS:
            price = Decimal("1000.00") + CYCLE[index % 4]
            if day in config.HOLIDAYS:
                price = price * Decimal("1.6")
            rows.append({"biz_date": day.isoformat(), "stratum": "restday",
                         "revenue_net": str(price)})
            index += 1
        day += dt.timedelta(days=1)
    return rows


def test_cards_match_confirmed_events(review):
    # 一张确认事件一张卡；逐日证据覆盖整个事件区间
    assert len(review["cards"]) == review["summary"]["events"] == 7
    keys = {(card["series"], card["start"]) for card in review["cards"]}
    assert ("R02/ota", "2026-11-18") in keys
    for card in review["cards"]:
        assert len(card["evidence"]) == card["days"]
        assert card["evidence"][0]["biz_date"] == card["start"]


def test_review_baseline_drops_holiday_points():
    # 节假日不并入休息日基线：10 个节假日点被剔除，中位数只由普通周末决定
    rows = _restday_rows()
    base = baseline.review_baseline(rows, len(rows) - 1, 28)
    window = baseline.review_window(rows, len(rows) - 1, "restday", 28)
    naive = madz.median([Decimal(row["revenue_net"]) for row in window])
    clean = [Decimal(row["revenue_net"]) for row in window
             if dt.date.fromisoformat(row["biz_date"]) not in config.HOLIDAYS]
    assert base["points"] == 28
    assert base["holiday_points"] == 10
    assert base["used_points"] == 18
    assert base["median"] == madz.median(clean)
    assert base["median"] < naive
    assert base["dispersion"] > 0


def test_holiday_share_marks_thin_stratum():
    rows = _restday_rows()
    base = baseline.review_baseline(rows, len(rows) - 1, 28)
    far = baseline.holiday_flag(base, Decimal("500"), Decimal("3.5"))
    near = baseline.holiday_flag(base, Decimal("990"), Decimal("3.5"))
    assert far["code"] == reasons.REVIEW_HOLIDAY_THIN and far["downgrade"] is False
    assert near["code"] == reasons.REVIEW_HOLIDAY_THIN and near["downgrade"] is True
    assert "剔除后 |z| 不足阈值" in near["detail"]
    plain = [{"biz_date": day, "stratum": "restday", "revenue_net": "1000.00"}
             for day in ("2026-11-01", "2026-11-07", "2026-11-08", "2026-11-14")]
    quiet = baseline.review_baseline(plain, len(plain) - 1, 28)
    assert quiet["holiday_points"] == 0
    assert baseline.holiday_flag(quiet, Decimal("990"), Decimal("3.5")) is None


def test_mad_zero_returns_code_not_division():
    # TRAP-27：基线全等时除法会炸，规则层必须先返回退化码
    rows = [{"biz_date": (dt.date(2026, 9, 1) + dt.timedelta(days=index)).isoformat(),
             "stratum": "workday", "revenue_net": "1000.00"} for index in range(16)]
    base = baseline.review_baseline(rows, 15, 28)
    assert (base["mad"], base["meanad"], base["method"]) == (Decimal("0"), Decimal("0"), "zero")
    z, code = baseline.review_z(Decimal("1500"), base)
    assert z is None and code == reasons.REVIEW_NO_DISPERSION
    with pytest.raises(DivisionByZero):
        Decimal("0.6745") * (Decimal("1500") - base["median"]) / base["dispersion"]


def test_flat_series_card_flagged_degenerate(settings, store):
    # TRAP-27 落到卡上：剔过节假日后基线全等，卡给 R_NO_DISPERSION 且复核 z 为空
    run = batch_service.run_batch(settings, store, _flat_csv(), "flat.csv")
    result = service.review(settings, store, run["run_id"])
    card = next(item for item in result["cards"] if item["start"] == "2026-11-14")
    assert card["baseline"]["method"] == "zero"
    assert card["baseline"]["z"] is None
    assert card["baseline"]["holiday_points"] == 10
    codes = [flag["code"] for flag in card["flags"]]
    assert reasons.REVIEW_NO_DISPERSION in codes
    assert card["confidence"] == "low"
    assert result["summary"]["degenerate"] == 3


def test_card_without_base_window_marks_zero_base(settings):
    # 基期没有房量时整组进残差，动作退到「补齐基期房量」，不硬编一个主因子
    points = [
        {"hotel_code": "X09", "channel": "ota", "biz_date": "2026-11-03",
         "stratum": "workday", "rooms_available": 100, "rooms_sold": 60,
         "adr": Decimal("500"), "revenue_net": Decimal("30000")},
        {"hotel_code": "X09", "channel": "ota", "biz_date": "2026-11-04",
         "stratum": "workday", "rooms_available": 100, "rooms_sold": 55,
         "adr": Decimal("500"), "revenue_net": Decimal("27500")},
    ]
    event = {"series": "X09/ota", "hotel_code": "X09", "channel": "ota",
             "start": "2026-11-03", "end": "2026-11-04", "days": 2,
             "direction": "low", "stratum": "workday", "delta": "-2500.00",
             "peak_z": "-6.1000"}
    card = service.card(event, points, [], settings, {"events": []}, [])
    assert card["factors"]["ok"] is False
    assert card["factors"]["code"] == "A_ZERO_BASE"
    assert [action["code"] for action in card["actions"]] == ["A_BASE"]
    assert reasons.REVIEW_ZERO_BASE in [flag["code"] for flag in card["flags"]]


def test_correlated_hotels_form_one_group():
    # 同一天同向、门店互不相同才算一组；同序列多天、方向相反都不算
    detections = [
        {"series": "H01/ota", "hotel_code": "H01", "channel": "ota",
         "biz_date": "2026-11-18", "direction": "low", "status": "confirmed"},
        {"series": "H02/ota", "hotel_code": "H02", "channel": "ota",
         "biz_date": "2026-11-18", "direction": "low", "status": "confirmed"},
        {"series": "H01/ota", "hotel_code": "H01", "channel": "ota",
         "biz_date": "2026-11-18", "direction": "high", "status": "confirmed"},
        {"series": "H03/member", "hotel_code": "H03", "channel": "member",
         "biz_date": "2026-11-19", "direction": "high", "status": "candidate"},
    ]
    groups = corr.clusters(detections)
    assert len(groups) == 1
    assert groups[0]["hotels"] == ["H01", "H02"]
    assert groups[0]["days"] == 1
    assert groups[0]["scope"] == "channel"
    assert corr.count_hotels(groups) == 2


def test_card_carries_correlation_and_peers(review):
    card = next(item for item in review["cards"]
                if item["event_id"] == "EV-R02-ota-20261118")
    assert reasons.REVIEW_CORRELATED in [flag["code"] for flag in card["flags"]]
    assert card["group_id"] == "GRP-20261118-R02-R03"
    assert [peer["series"] for peer in card["peers"]] == ["R03/ota"]
    assert card["peers"][0]["relation"] == corr.RELATION_CHANNEL
    assert card["confidence"] == "medium"


def test_actions_follow_dominant_factor(review):
    # 出租率下滑的卡动作指向超售核对；不能所有卡都给同一个动作
    card = next(item for item in review["cards"]
                if item["event_id"] == "EV-R02-ota-20261118")
    assert card["factors"]["dominant"] == "occ"
    assert [action["code"] for action in card["actions"]] == ["A_OCC"]


def test_low_flag_means_low_confidence(review):
    for card in review["cards"]:
        levels = {flag["level"] for flag in card["flags"]}
        if "low" in levels:
            assert card["confidence"] == "low"
        elif "medium" in levels:
            assert card["confidence"] == "medium"
        else:
            assert card["confidence"] == "high"
    assert review["summary"]["low"] == 5


def test_cards_csv_one_row_per_card(review, review_client):
    text = cards_csv.cards_csv(review["cards"])
    assert text.startswith("\ufeff")
    lines = text.splitlines()
    assert lines[0].replace("\ufeff", "").split(",")[:4] == [
        "event_id", "series", "start", "end"]
    assert len(lines) == len(review["cards"]) + 1
    assert "R_HOLIDAY_THIN" in text or "R_CORRELATED" in text
    response = review_client.get("/api/review/cards.csv")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]


def test_card_endpoint_and_unknown_card(review_client, review):
    first = review["cards"][0]["event_id"]
    response = review_client.get("/api/review/" + first)
    assert response.status_code == 200
    assert response.json()["event_id"] == first
    missing = review_client.get("/api/review/EV-nope-20260101")
    assert missing.status_code == 404
    assert missing.json()["error"] == "E_CARD_NOT_FOUND"


def test_review_api_without_run(tmp_path):
    settings = config.Settings(db_path=str(tmp_path / "empty.sqlite"),
                               sample_dir=str(config.Settings().sample_dir),
                               seed_on_start=False)
    with TestClient(create_app(settings)) as test_client:
        response = test_client.get("/api/review")
    assert response.status_code == 404
    assert response.json()["error"] == "E_NO_RUN"


def test_review_page_renders_panels(review_client):
    page = review_client.get("/review")
    assert page.status_code == 200
    for text in ("敏感性矩阵", "同批异常", "复盘卡", "逐日证据", "同向对照"):
        assert text in page.text
