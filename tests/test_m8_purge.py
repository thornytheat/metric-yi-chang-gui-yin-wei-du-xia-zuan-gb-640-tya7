"""M8 门店退网不可逆清除：凭据承接、四账差额、残留、中断恢复、清除优先、重导不恢复。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.errors import Conflict
from app.modules.m5_attrib import service as batch_service
from app.modules.m8_purge import service as purge_service


# ------------------------------------------------------------------
# 1. 不可逆清除：明细消失、凭据不含门店标识、账目可复核
# ------------------------------------------------------------------
def test_purge_removes_hotel_points_and_quarantine(settings, store, run):
    hotel = "H02"
    before_points = len(store.points_for_hotel(run["run_id"], hotel))
    before_quarantine = len(store.quarantine_for_hotel(run["run_id"], hotel))
    assert before_points > 0 and before_quarantine > 0

    report = purge_service.purge_hotel(settings, store, hotel)

    assert report["state"] == "done"
    assert report["balanced"] is True
    assert store.points_for_hotel(run["run_id"], hotel) == []
    assert store.quarantine_for_hotel(run["run_id"], hotel) == []
    # 库里任何位置都不再能按门店编码定位明细
    raw_points = store.load_points(run["run_id"])
    assert all(point["hotel_code"] != hotel for point in raw_points)
    raw_quarantine = store.load_quarantine(run["run_id"])
    assert all(entry.get("hotel_code") != hotel for entry in raw_quarantine)


def test_vouchers_have_no_hotel_identifier(settings, store, run):
    report = purge_service.purge_hotel(settings, store, "H01")
    point_vouchers = report["point_vouchers"]
    quarantine_vouchers = report["quarantine_vouchers"]
    assert point_vouchers, "凭据不能为空"
    for item in point_vouchers:
        assert "hotel_code" not in item and "hotel_name" not in item
        assert set(item) == {"purge_id", "run_id", "channel", "biz_date",
                             "point_count", "rooms_available", "rooms_sold",
                             "revenue_net"}
    for item in quarantine_vouchers:
        assert "hotel_code" not in item and "hotel_name" not in item


def test_voucher_revenue_equals_removed_detail_revenue(settings, store, run):
    hotel = "H03"
    removed = store.points_for_hotel(run["run_id"], hotel)
    expected_revenue = sum((Decimal(point["revenue_net"]) for point in removed),
                           Decimal("0"))
    expected_count = len(removed)

    report = purge_service.purge_hotel(settings, store, hotel)

    voucher_revenue = sum((Decimal(item["revenue_net"])
                           for item in report["point_vouchers"]), Decimal("0"))
    voucher_count = sum(item["point_count"] for item in report["point_vouchers"])
    assert voucher_revenue == expected_revenue
    assert voucher_count == expected_count


def test_report_differences_match_vouchers_and_balanced(settings, store, run):
    before_accounts = dict(run["accounts"])
    report = purge_service.purge_hotel(settings, store, "H04")

    differences = report["differences"]
    voucher = report["voucher_totals"]
    assert differences["points_valid"] == voucher["points"]
    # 凭据同时承接隔离行与被覆盖行（两者都能定位门店）
    assert (differences["rows_quarantine"] + differences["rows_superseded"]
            == voucher["quarantine_entries"])
    assert Decimal(differences["revenue_accepted"]) == Decimal(voucher["revenue_net"])
    assert all(check["ok"] for check in report["variances"])
    # 清除后账目按剩余数据复算，前后差额恰好是被承接的量
    after = store.get_run(run["run_id"])["summary"]["accounts"]
    assert before_accounts["points_valid"] - after["points_valid"] == voucher["points"]
    assert (Decimal(before_accounts["revenue_accepted"])
            - Decimal(after["revenue_accepted"])) == Decimal(voucher["revenue_net"])
    # 四账等式在清除后仍然成立
    for check in after_checks(after):
        assert check["ok"], check


def after_checks(accounts: dict) -> list[dict]:
    from app.modules.m3_quality.service import account_checks
    return account_checks(accounts)


def test_residual_check_finds_no_trace(settings, store, run):
    report = purge_service.purge_hotel(settings, store, "H02")
    for item in report["residuals"]:
        assert item["ok"] is True, item
        assert item["count"] == 0


def test_detection_and_review_have_no_purged_hotel(settings, store, run):
    from app.modules.m4_detect import service as detect_service
    from app.modules.m7_review import service as review_service

    purge_service.purge_hotel(settings, store, "H02")
    points = batch_service.load_view(store, run["run_id"])
    detections = detect_service.detect(points, settings)["detections"]
    assert all(item["hotel_code"] != "H02" for item in detections)
    review = review_service.review(settings, store, run["run_id"])
    assert all(card["hotel_code"] != "H02" for card in review["cards"])


# ------------------------------------------------------------------
# 2. 不可逆：同门店重跑返回原报告，不恢复任何明细
# ------------------------------------------------------------------
def test_repeat_purge_is_idempotent_and_never_restores(settings, store, run):
    first = purge_service.purge_hotel(settings, store, "H01")
    remaining_after_first = len(store.load_points(run["run_id"]))
    second = purge_service.purge_hotel(settings, store, "H01")

    assert second["reused"] is True
    assert second["purge_id"] == first["purge_id"]
    assert second["balanced"] is True
    assert second["differences"] == first["differences"]
    assert len(store.load_points(run["run_id"])) == remaining_after_first
    assert store.points_for_hotel(run["run_id"], "H01") == []
    purges = store.list_purges()
    assert len(purges) == 1


def test_same_file_reimport_does_not_restore_decommissioned_hotel(settings, store,
                                                                   run, dirty_bytes):
    purge_service.purge_hotel(settings, store, "H02")
    # 同源重导命中同一 run_id，直接复用且不含 H02
    summary = batch_service.run_batch(settings, store, dirty_bytes, "daily_metrics_dirty.csv")
    assert summary["reused"] is True
    assert store.points_for_hotel(run["run_id"], "H02") == []


def test_new_batch_filters_decommissioned_hotel_rows(settings, store, run, dirty_bytes):
    purge_service.purge_hotel(settings, store, "H02")
    # 重复一行改变 sha 造新 run_id，强制走完整采信流水线，退网门店行在采信阶段被剔除
    lines = dirty_bytes.decode("utf-8-sig").splitlines()
    lines.insert(1, lines[1])
    new_data = ("﻿" + "\n".join(lines) + "\n").encode("utf-8")

    summary = batch_service.run_batch(settings, store, new_data, "rerun.csv")
    assert summary["reused"] is False
    new_run_id = summary["run_id"]
    assert store.points_for_hotel(new_run_id, "H02") == []
    assert all(point["hotel_code"] != "H02"
               for point in store.load_points(new_run_id))
    assert any(notice["code"] == "N_HOTEL_PURGED" for notice in summary["notices"])
    assert "H02" in summary["purged_hotels"]


# ------------------------------------------------------------------
# 3. 中断恢复：半途崩溃后续跑不产生半清除、不重复承接
# ------------------------------------------------------------------
def test_interrupted_purge_resumes_and_finishes(settings, store, run, monkeypatch):
    hotel = "H04"
    purge_id = purge_service.purge_id_for(hotel)
    original = purge_service._summary_without_hotel

    calls = {"n": 0}

    def flaky_summary(cfg, db, run_id, hotel_code, pid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash mid-purge")
        return original(cfg, db, run_id, hotel_code, pid)

    monkeypatch.setattr(purge_service, "_summary_without_hotel", flaky_summary)
    with pytest.raises(RuntimeError, match="simulated crash"):
        purge_service.purge_hotel(settings, store, hotel)

    # 崩溃发生在事务之前：任务登记为 running，且该批次没有任何半清除写入
    stalled = store.get_purge(purge_id)
    assert stalled["state"] == "running"
    assert store.load_point_vouchers(purge_id) == []
    # 读路径仍看到完整旧账，没有半清除明细
    assert len(store.points_for_hotel(run["run_id"], hotel)) > 0

    monkeypatch.undo()
    report = purge_service.purge_hotel(settings, store, hotel)
    assert report["state"] == "done"
    assert store.get_purge(purge_id)["state"] == "done"
    assert store.points_for_hotel(run["run_id"], hotel) == []
    # 续跑不重复承接：凭据行数与被删明细一一对应
    vouchers = store.load_point_vouchers(purge_id)
    assert sum(item["point_count"] for item in vouchers) == report["voucher_totals"]["points"]
    assert all(item["ok"] for item in report["residuals"])


def test_interrupted_after_commit_resumes_remaining_runs(settings, store, dirty_bytes):
    # 两批次都含同一门店：第一个批次事务提交后、第二个批次处理前崩溃，续跑只补未完成批次。
    first = batch_service.run_batch(settings, store, dirty_bytes, "dirty.csv")
    lines = dirty_bytes.decode("utf-8-sig").splitlines()
    # 取 H01 的前 20 行另存为新文件，造一个同样含 H01 的新批次
    h01_lines = [line for line in lines[1:] if line.startswith("H01,")][:20]
    second_data = ("﻿" + lines[0] + "\n" + "\n".join(h01_lines) + "\n").encode("utf-8")
    second = batch_service.run_batch(settings, store, second_data, "h01-slice.csv")
    assert first["run_id"] != second["run_id"]
    hotel = "H01"
    purge_id = purge_service.purge_id_for(hotel)
    original = purge_service._summary_without_hotel
    calls = {"n": 0}

    def flaky_summary(cfg, db, run_id, hotel_code, pid):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated crash between runs")
        return original(cfg, db, run_id, hotel_code, pid)

    import app.modules.m8_purge.service as purge_mod
    purge_mod._summary_without_hotel = flaky_summary
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            purge_service.purge_hotel(settings, store, hotel)
    finally:
        purge_mod._summary_without_hotel = original
    assert store.get_purge(purge_id)["state"] == "running"
    # 第一个批次已提交清除，第二个批次未受影响
    assert store.points_for_hotel(first["run_id"], hotel) == []
    assert len(store.points_for_hotel(second["run_id"], hotel)) == 20

    report = purge_service.purge_hotel(settings, store, hotel)
    assert report["balanced"] is True
    assert store.points_for_hotel(first["run_id"], hotel) == []
    assert store.points_for_hotel(second["run_id"], hotel) == []
    # 第一个批次走 skipped 续跑，凭据不重复
    keys = [(v["run_id"], v["channel"], v["biz_date"])
            for v in store.load_point_vouchers(purge_id)]
    assert len(keys) == len(set(keys))


def test_no_half_purged_reads_during_running_state(settings, store, run):
    # 直接登记一个 running 任务模拟“清除进行中”：此时读路径看到的仍是完整旧账，
    # 绝不会读到删了一半的明细（删除只在凭据落库后的同一把写锁内发生）。
    purge_id = purge_service.purge_id_for("H03")
    store.create_purge(purge_id, "H03", purge_service._stamp())
    assert store.purge_running()["hotel_code"] == "H03"
    points = store.load_points(run["run_id"])
    assert any(point["hotel_code"] == "H03" for point in points)


# ------------------------------------------------------------------
# 4. 清除优先：与导入并发时导入让锁
# ------------------------------------------------------------------
def test_import_blocked_while_purge_running(settings, store, run, dirty_bytes):
    purge_id = purge_service.purge_id_for("H01")
    store.create_purge(purge_id, "H01", purge_service._stamp())
    with pytest.raises(Conflict) as exc:
        batch_service.run_batch(settings, store, dirty_bytes, "daily_metrics_dirty.csv")
    assert exc.value.code == "E_PURGE_RUNNING"
    # 导入被拒后原数据原样可读
    assert len(store.load_points(run["run_id"])) == run["accounts"]["points_valid"]


# ------------------------------------------------------------------
# 5. 多批次：清除覆盖全部批次，报告逐批次给差额
# ------------------------------------------------------------------
def test_purge_covers_multiple_runs(settings, store, dirty_bytes):
    first = batch_service.run_batch(settings, store, dirty_bytes, "dirty.csv")
    lines = dirty_bytes.decode("utf-8-sig").splitlines()
    h01_lines = [line for line in lines[1:] if line.startswith("H01,")][:20]
    second_data = ("﻿" + lines[0] + "\n" + "\n".join(h01_lines) + "\n").encode("utf-8")
    second = batch_service.run_batch(settings, store, second_data, "h01-slice.csv")
    assert first["run_id"] != second["run_id"]

    report = purge_service.purge_hotel(settings, store, "H01")
    run_ids = {item["run_id"] for item in report["runs"]}
    assert run_ids == {first["run_id"], second["run_id"]}
    for run_id in run_ids:
        assert store.points_for_hotel(run_id, "H01") == []
    assert all(item["ok"] for item in report["variances"])
    # 凭据按批次隔离，财务仍能复核每个旧批次
    voucher_runs = {item["run_id"] for item in report["point_vouchers"]}
    assert voucher_runs <= run_ids and voucher_runs
    assert len(voucher_runs) == 2


# ------------------------------------------------------------------
# 6. API 层：状态、409、404、凭据 CSV 无门店列
# ------------------------------------------------------------------
def test_api_purge_flow_and_voucher_csv(client, run):
    resp = client.post("/api/purges", json={"hotel_code": "H02"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    purge_id = body["purge_id"]

    status = client.get("/api/purges").json()
    assert "H02" in status["decommissioned"]
    assert status["running"] == ""

    detail = client.get(f"/api/purges/{purge_id}")
    assert detail.status_code == 200

    points_csv = client.get(f"/api/purges/{purge_id}/vouchers/points.csv").text
    header = points_csv.splitlines()[0].replace("﻿", "")
    assert "hotel_code" not in header and "hotel_name" not in header
    assert "H02" not in points_csv  # 凭据内容本身不能定位门店

    quarantine_csv = client.get(f"/api/purges/{purge_id}/vouchers/quarantine.csv").text
    assert "H02" not in quarantine_csv

    # 重发同一门店：幂等返回原报告
    again = client.post("/api/purges", json={"hotel_code": "H02"})
    assert again.status_code == 200
    assert again.json()["reused"] is True


def test_api_import_conflict_during_purge(client, run, settings, store):
    purge_id = purge_service.purge_id_for("H01")
    store.create_purge(purge_id, "H01", purge_service._stamp())
    resp = client.post("/api/import", data={"sample": "daily_metrics_dirty.csv"})
    assert resp.status_code == 409
    assert resp.json()["error"] == "E_PURGE_RUNNING"


def test_api_purge_unknown_id_404(client, run):
    resp = client.get("/api/purges/purge-nope")
    assert resp.status_code == 404
    assert resp.json()["error"] == "E_PURGE_NOT_FOUND"


def test_api_purge_blank_hotel_400(client, run):
    resp = client.post("/api/purges", json={"hotel_code": "  "})
    assert resp.status_code == 400
    assert resp.json()["error"] == "E_BAD_HOTEL"


def test_purge_unknown_hotel_keeps_data_and_is_idempotent(settings, store, run):
    report = purge_service.purge_hotel(settings, store, "H99")
    assert report["balanced"] is True
    assert report["voucher_totals"]["points"] == 0
    assert all(item["ok"] for item in report["residuals"])
    # 没有该门店时原始明细一条不动
    assert len(store.load_points(run["run_id"])) == run["accounts"]["points_valid"]
    again = purge_service.purge_hotel(settings, store, "H99")
    assert again["reused"] is True


def test_transaction_rollback_leaves_no_half_purge(settings, store, run, monkeypatch):
    # 事务内删除阶段抛错：整体回滚，凭据与删除都不落库，读路径仍是完整旧账
    hotel = "H03"

    def failing_txn(run_id, hotel_code, purge_id, pv, qv, summary):
        raise RuntimeError("disk full during transaction")

    monkeypatch.setattr(store, "purge_run_in_transaction", failing_txn)
    with pytest.raises(RuntimeError, match="disk full"):
        purge_service.purge_hotel(settings, store, hotel)
    monkeypatch.undo()

    purge_id = purge_service.purge_id_for(hotel)
    assert store.get_purge(purge_id)["state"] == "running"
    assert store.load_point_vouchers(purge_id) == []
    # 明细与隔离完整保留，没有半清除
    assert len(store.points_for_hotel(run["run_id"], hotel)) > 0
    assert len(store.quarantine_for_hotel(run["run_id"], hotel)) > 0
    # 续跑正常完成
    report = purge_service.purge_hotel(settings, store, hotel)
    assert report["balanced"] is True
    assert store.points_for_hotel(run["run_id"], hotel) == []


def test_finance_reconcile_still_balanced_after_purge(client, run):
    # 财务复核旧批次：清除后 /api/reconcile 四账逐条平，收入合计等于剩余明细
    client.post("/api/purges", json={"hotel_code": "H03"})
    reconcile = client.get("/api/reconcile").json()
    assert reconcile["identity_ok"] is True
    for check in reconcile["checks"]:
        assert check["ok"] is True
    report = client.get("/api/report").json()
    accounts = report["accounts"]
    # 金额账：报告收入合计 = 库里剩余有效点净收入之和
    store = client.app.state.anomalyattr.store
    points = store.load_points(run["run_id"])
    total = sum((Decimal(p["revenue_net"]) for p in points), Decimal("0"))
    assert Decimal(accounts["revenue_accepted"]) == total
    # 行账守恒：读入 = 采信 + 被覆盖 + 隔离
    assert accounts["rows_read"] == (accounts["rows_accepted"]
                                     + accounts["rows_superseded"]
                                     + accounts["rows_quarantine"])


def test_purge_page_renders(client, run):
    resp = client.get("/purge")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "退网" in resp.text
