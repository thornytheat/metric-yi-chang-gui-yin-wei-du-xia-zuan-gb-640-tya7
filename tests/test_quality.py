"""M3 质量隔离：迟到标记、重复快照归因、三账守恒、顺序无关。"""
from __future__ import annotations

from decimal import Decimal

from app.modules.m2_calendar.service import CoercedRow
from app.modules.m3_quality import dupes, quarantine, service
from tests.fixtures import expected as fx


def _row(line_no, revenue, snapshot, key=("H01", "ota", "2026-11-03")) -> CoercedRow:
    import datetime as dt

    return CoercedRow(
        hotel_code=key[0], hotel_name=key[0], channel=key[1],
        biz_date=dt.date.fromisoformat(key[2]), stratum="workday",
        rooms_available=180, rooms_sold=120, adr=Decimal("700"),
        revenue_net=Decimal(revenue), snapshot_utc=snapshot,
        snapshot_local=snapshot.astimezone(dt.timezone(dt.timedelta(hours=8))).date(),
        late_days=0, line_no=line_no)


def test_late_arrival_is_kept_and_marked(run):
    # TRAP-13：迟到数据不能因为水位推进就丢掉
    accounts = run["accounts"]
    assert accounts["late_points"] == fx.LATE_POINTS
    assert accounts["out_of_window_points"] == fx.OUT_OF_WINDOW_POINTS
    assert any(mark["code"] == "L_LATE_ARRIVAL" for mark in run["late_marks"])
    late = [mark for mark in run["late_marks"] if mark["code"] == "L_OUT_OF_WINDOW"]
    assert late and late[0]["late_days"] == 9


def test_duplicate_snapshot_later_wins(run, store):
    # TRAP-14：同键多条按快照时间取最晚，不按行序覆盖
    points = store.load_points(run["run_id"])
    target = [point for point in points
              if point["hotel_code"] == "H01" and point["channel"] == "ota"
              and point["biz_date"] == "2026-10-20"]
    assert len(target) == 1
    superseded = [item for item in run["duplicates"] if item["biz_date"] == "2026-10-20"]
    assert superseded and superseded[0]["code"] == fx.SUPERSEDED_CODE
    assert Decimal(target[0]["revenue_net"]) != Decimal(superseded[0]["revenue_net"])


def test_duplicate_conflict_isolated_both_rows(run):
    # TRAP-14：同一快照时间给出不同数值时整键隔离，不猜哪条为准
    conflicts = [item for item in run["conflicts"] if item["code"] == fx.CONFLICT_CODE]
    assert len(conflicts) == 2
    keys = {(item["hotel_code"], item["channel"], item["biz_date"]) for item in conflicts}
    assert keys == {("H02", "member", "2026-11-10")}
    reason_codes = {row["code"]: row["count"] for row in run["reason_counts"]}
    assert reason_codes[fx.CONFLICT_CODE] == 2


def test_naive_last_write_wins_keeps_conflict(store, run):
    # TRAP-14 反例：按行序后写覆盖会保留一条，把冲突悄悄藏起来
    import app.modules.m5_attrib.service as batch
    from tests.test_quality import _row as make_row
    import datetime as dt

    stamp = dt.datetime(2026, 11, 11, 0, 0, tzinfo=dt.timezone.utc)
    rows = [_row(3, "1000.00", stamp), _row(4, "1999.99", stamp)]
    naive = dupes.naive_last_write_wins(rows)
    assert len(naive) == 1
    resolved = dupes.resolve(rows)
    assert resolved.winners == []
    assert len(resolved.conflicts) == 2
    assert batch is not None


def test_rows_identity_holds(run):
    # 不变量：读入行 = 采信 + 被覆盖 + 隔离
    accounts = run["accounts"]
    assert accounts["rows_read"] == (accounts["rows_accepted"]
                                     + accounts["rows_superseded"]
                                     + accounts["rows_quarantine"])
    assert accounts["rows_read"] == 1016
    assert accounts["rows_quarantine"] == 11


def test_points_identity_holds(run):
    # 不变量：网格点 = 有效 + 隔离 + 缺失
    accounts = run["accounts"]
    assert accounts["points_grid"] == fx.GRID_POINTS
    assert accounts["points_grid"] == (accounts["points_valid"]
                                       + accounts["points_rejected"]
                                       + accounts["points_missing"])
    assert accounts["points_missing"] == fx.MISSING_BUCKETS


def test_account_checks_report_balanced(run):
    for check in run["checks"]:
        assert check["ok"] is True, check
        assert check["left"] == check["right"]


def test_revenue_identity_holds(run, store):
    # 金额账：采信行合计 = 有效点合计（Decimal 两位）
    points = store.load_points(run["run_id"])
    total = sum((Decimal(point["revenue_net"]) for point in points), Decimal("0"))
    assert total == Decimal(run["accounts"]["revenue_accepted"])
    assert len(points) == run["accounts"]["points_valid"]


def test_shuffled_input_gives_same_accounts(settings, dirty_bytes):
    # 顺序无关：打乱物理行序后账目一致（排序键是快照时间而不是行号）
    from app.modules.m1_ingest import service as ingest_service
    from app.modules.m2_calendar import service as coerce_service

    def evaluate(data: bytes):
        ingest = ingest_service.ingest(data, "shuffled.csv", settings)
        coerced, rejects = coerce_service.coerce_rows(ingest.rows, settings)
        return service.evaluate(coerced, list(ingest.rejects) + rejects, settings,
                                ingest.total_records)

    lines = dirty_bytes.decode("utf-8-sig").splitlines()
    shuffled = "\n".join([lines[0]] + list(reversed(lines[1:]))) + "\n"
    original = evaluate(dirty_bytes)
    reversed_run = evaluate(shuffled.encode("utf-8"))
    assert original.accounts == reversed_run.accounts
    assert original.grid == reversed_run.grid


def test_quarantine_csv_has_evidence_columns(run, store):
    entries = store.load_quarantine(run["run_id"])
    text = quarantine.to_csv(entries)
    header = text.splitlines()[0].replace("\ufeff", "")
    assert header.split(",")[:6] == ["line_no", "code", "label", "field", "raw", "detail"]
    assert "Q_DUP_CONFLICT" in text
    assert "Q_COLUMN_COUNT" in text


def test_reason_counts_grouped_by_account(run):
    counts = {row["code"]: row for row in run["reason_counts"]}
    assert counts["Q_SENTINEL"]["count"] == 3
    assert counts["Q_SENTINEL"]["account"] == "quarantine"
    assert counts["D_SUPERSEDED"]["account"] == "duplicate"
    assert sum(row["count"] for row in run["reason_counts"]) == (
        run["accounts"]["rows_quarantine"] + run["accounts"]["rows_superseded"])


def test_notices_include_bom_only(run):
    codes = {item["code"] for item in run["notices"]}
    assert codes == {"N_BOM_STRIPPED"}


def test_late_window_boundary_exactly_three_days():
    # TRAP-13 边界：恰好 3 天仍在补报窗口内，第 4 天才算超窗（不多算一天也不少算）
    import datetime as dt

    from app import config
    from app.modules.m3_quality import rules
    from tests.test_quality import _row as make_row

    settings = config.Settings(db_path="/tmp/unused.sqlite", seed_on_start=False)
    snapshot = dt.datetime(2026, 11, 5, 22, tzinfo=dt.timezone.utc)
    row = make_row(1, "1000", snapshot)
    row.late_days = 3
    assert [mark["code"] for mark in rules.mark_lateness([row], settings)] == [
        "L_LATE_ARRIVAL"]
    assert row.late_flag is True and row.out_of_window is False
    row.late_days = 4
    assert [mark["code"] for mark in rules.mark_lateness([row], settings)] == [
        "L_OUT_OF_WINDOW"]
    assert row.out_of_window is True
