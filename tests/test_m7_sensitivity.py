"""M7 敏感性：九组合、保留判据、翻转标注、阈值单调与账户守恒。"""
from __future__ import annotations

from decimal import Decimal

from app.modules.m7_review import service, sensitiv


def test_axis_has_nine_unique_combinations():
    combos = sensitiv.combos()
    assert len(combos) == 9
    assert [combo["key"] for combo in combos] == [
        "21x2.5", "21x3.5", "21x4.5",
        "28x2.5", "28x3.5", "28x4.5",
        "35x2.5", "35x3.5", "35x4.5",
    ]
    assert len({combo["key"] for combo in combos}) == 9


def test_base_key_matches_settings(settings):
    assert sensitiv.base_key(settings) == "28x3.5"
    assert sensitiv.axis_label(settings) == "28 点 × |z| 3.5"


def test_holds_needs_half_of_event_days():
    # 两天事件至少一天仍为确认且同向才算保留，一天都不剩就是翻转
    event = {"series": "H01/ota", "start": "2026-11-14", "end": "2026-11-15",
             "days": 2, "direction": "low"}
    one_day = [{"series": "H01/ota", "biz_date": "2026-11-14", "status": "confirmed",
                "direction": "low"}]
    wrong_dir = [{"series": "H01/ota", "biz_date": "2026-11-14", "status": "confirmed",
                  "direction": "high"}]
    assert sensitiv.holds(event, one_day) is True
    assert sensitiv.holds(event, wrong_dir) is False
    assert sensitiv.kept_days(event, one_day) == 1


def test_flip_marks_unstable_and_lists_combo(review):
    # TRAP-28：基准组合里确认的事件，换 4.5 阈值就没了；结论必须标翻转
    base_event = {"series": "R01/member", "start": "2026-10-31", "end": "2026-11-01",
                  "days": 2, "direction": "low"}
    eid = sensitiv.event_id(base_event)
    rows = {row["event_id"]: row for row in review["sensitivity"]["events"]}
    assert rows[eid]["verdict"] == sensitiv.UNSTABLE
    assert rows[eid]["flip"] == 3
    assert "28x4.5" in rows[eid]["flipped"]
    assert any(combo["key"] == "28x4.5" and combo["confirmed_points"] < 14
               for combo in review["sensitivity"]["combos"])
    card = next(item for item in review["cards"] if item["event_id"] == eid)
    assert card["verdict"] == sensitiv.UNSTABLE
    assert [action["code"] for action in card["actions"]] == ["A_OCC", "A_RECHECK"]
    assert review["sensitivity"]["verdict"] == sensitiv.UNSTABLE


def test_threshold_monotonic_confirmed_points(review):
    # 不变量：阈值抬高，确认点只减不增；同一窗口下三档必须单调不增
    rows = review["sensitivity"]["combos"]
    by_window: dict[int, dict[str, int]] = {}
    for row in rows:
        by_window.setdefault(row["window"], {})[row["threshold"]] = row["confirmed_points"]
    for window, table in by_window.items():
        assert table["2.5"] >= table["3.5"] >= table["4.5"], window


def test_combos_account_for_every_event(review):
    # 守恒：每个事件在九个组合里恰好记一次保留或翻转
    axis = review["sensitivity"]
    total = len(axis["events"]) * 9
    assert all(row["hold"] + row["flip"] == 9 for row in axis["events"])
    assert sum(row["kept"] for row in axis["combos"]) + \
        sum(row["flipped"] for row in axis["combos"]) == total
    assert axis["hold_sum"] + axis["flip_sum"] == total
    assert axis["stable"] + axis["unstable"] == len(axis["events"])


def test_sensitivity_point_endpoint(review_client):
    response = review_client.get("/api/review/sensitivity?window=21&threshold=4.5")
    assert response.status_code == 200
    payload = response.json()
    assert payload["axis"]["window"] == 21
    assert payload["axis"]["threshold"] == "4.5"
    assert len(payload["events"]) == 7
    assert payload["held"] + payload["flipped"] == 7
    assert payload["summary"]["confirmed_points"] == 6
    base = review_client.get("/api/review/sensitivity").json()
    assert base["axis"]["window"] == 28 and base["axis"]["threshold"] == "3.5"
    assert base["flipped"] == 0


def test_sensitivity_bad_axis_is_4xx(review_client):
    narrow = review_client.get("/api/review/sensitivity?window=5&threshold=3.5")
    assert narrow.status_code == 400
    assert narrow.json()["error"] == "E_BAD_AXIS"
    text = review_client.get("/api/review/sensitivity?window=28&threshold=abc")
    assert text.status_code == 400
    assert text.json()["error"] == "E_BAD_AXIS"
    low = review_client.get("/api/review/sensitivity?window=28&threshold=0.5")
    assert low.status_code == 400


def test_parse_axis_rejects_out_of_range(settings):
    assert service.parse_axis("28", "3.5") == (28, Decimal("3.5"))
    for window, threshold in (("13", "3.5"), ("43", "3.5"), ("28", "11"), ("x", "3.5")):
        try:
            service.parse_axis(window, threshold)
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "status", None) == 400
        else:  # pragma: no cover - 不该走到这里
            raise AssertionError("越界参数没有被拒绝")
