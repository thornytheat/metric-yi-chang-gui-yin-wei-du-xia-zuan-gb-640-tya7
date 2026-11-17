"""门店退网不可逆清除：凭据承接、四账复算、可中断恢复。

清除不是“删行”：先把退网门店在每个批次里的有效点与隔离行按
「run × 渠道 × 业务日」聚成不含门店标识的汇总凭据，再删除明细，
随后按剩余数据重算四账与收入合计。报告给出清除前后每个账目的差额，
且差额必须与凭据合计逐项对上。

任务状态机（purges.state）：
  running ──完成──▶ done
中途崩溃后状态仍是 running，再次发起同一门店的清除会续跑：
凭据已落库的批次直接跳过明细处理，只补齐剩余批次与最终复算，
保证不产生半清除、也不重复承接。
"""
from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal

from app import config, reasons
from app.errors import DomainError
from app.modules.m3_quality import quarantine as quarantine_mod
from app.modules.m4_detect import service as detect_service
from app.modules.m5_attrib import service as batch_service

# 任务状态只有 running / done；崩溃恢复靠 purges.progress 里已提交批次的进度判断。
PURGE_RULES = "purge-rules-2026.11.1"


def purge_id_for(hotel_code: str) -> str:
    digest = hashlib.sha256(
        f"{hotel_code}|{PURGE_RULES}".encode("utf-8")).hexdigest()[:12]
    return "purge-" + digest


def _stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------
# 凭据聚合：不含门店标识，只留 run × 渠道 × 业务日的合计
# ------------------------------------------------------------------
def build_point_vouchers(points: list[dict], run_id: str) -> list[dict]:
    buckets: dict[tuple, dict] = {}
    for point in points:
        key = (point["channel"], point["biz_date"])
        bucket = buckets.setdefault(key, {
            "run_id": run_id, "channel": key[0], "biz_date": key[1],
            "point_count": 0, "rooms_available": 0, "rooms_sold": 0,
            "revenue_net": Decimal("0"),
        })
        bucket["point_count"] += 1
        bucket["rooms_available"] += int(point["rooms_available"])
        bucket["rooms_sold"] += int(point["rooms_sold"])
        bucket["revenue_net"] += Decimal(point["revenue_net"])
    out = []
    for key in sorted(buckets):
        item = buckets[key]
        item["revenue_net"] = str(item["revenue_net"])
        out.append(item)
    return out


def build_quarantine_vouchers(entries: list[dict], run_id: str) -> list[dict]:
    tally: dict[str, int] = {}
    for entry in entries:
        tally[entry["code"]] = tally.get(entry["code"], 0) + 1
    return [{"run_id": run_id, "code": code, "entry_count": count}
            for code, count in sorted(tally.items())]


def voucher_totals(point_vouchers: list[dict],
                   quarantine_vouchers: list[dict]) -> dict:
    return {
        "points": sum(item["point_count"] for item in point_vouchers),
        "rooms_available": sum(item["rooms_available"] for item in point_vouchers),
        "rooms_sold": sum(item["rooms_sold"] for item in point_vouchers),
        "revenue_net": str(sum((Decimal(item["revenue_net"])
                                for item in point_vouchers), Decimal("0"))),
        "quarantine_entries": sum(item["entry_count"] for item in quarantine_vouchers),
    }


# ------------------------------------------------------------------
# 清除后四账复算：只看剩余明细，结论与清除前同一条流水线
# ------------------------------------------------------------------
def _recompute_grid(remaining_points: list[dict], remaining_quarantine: list[dict]) -> dict:
    """与 M3 同一套网格口径：门店 × 渠道 × 业务日，隔离点占位。"""
    combos: set[tuple] = set()
    days: set[str] = set()
    occupied: set[tuple] = set()
    for entry in remaining_quarantine:
        hotel = entry.get("hotel_code")
        channel = entry.get("channel")
        day = entry.get("biz_date")
        if not hotel or not channel or not day:
            continue
        combos.add((hotel, channel))
        days.add(day)
        if reasons.account(entry["code"]) == "quarantine":
            occupied.add((hotel, channel, day))
    for point in remaining_points:
        combos.add((point["hotel_code"], point["channel"]))
        days.add(point["biz_date"])
        occupied.add((point["hotel_code"], point["channel"], point["biz_date"]))
    grid_points = len(combos) * len(days) if combos and days else 0
    points_valid = len(remaining_points)
    points_rejected = max(0, len(occupied) - points_valid)
    points_missing = max(0, grid_points - points_valid - points_rejected)
    return {
        "combos": len(combos),
        "days": len(days),
        "start": min(days) if days else "",
        "end": max(days) if days else "",
        "points_grid": grid_points,
        "points_valid": points_valid,
        "points_rejected": points_rejected,
        "points_missing": points_missing,
    }


def _account_checks(accounts: dict) -> list[dict]:
    from app.modules.m3_quality.service import account_checks
    return account_checks(accounts)


# ------------------------------------------------------------------
# 主流程：可恢复、读不到半清除、报告带前后差额
# ------------------------------------------------------------------
def purge_hotel(settings: config.Settings, store, hotel_code: str) -> dict:
    hotel_code = (hotel_code or "").strip()
    if not hotel_code:
        raise DomainError("门店编码不能为空", code="E_BAD_HOTEL")
    with store.write_lock:
        existing = store.get_purge_by_hotel(hotel_code)
        if existing is not None and existing["state"] == "done":
            # 不可逆：同门店重跑只回原报告，不恢复、不重建。
            return _finished_payload(existing, store, reused=True)
        if existing is not None and existing["state"] == "running":
            purge_id = existing["purge_id"]
            created_at = existing["created_at"]
        else:
            purge_id = purge_id_for(hotel_code)
            created_at = _stamp()
            store.create_purge(purge_id, hotel_code, created_at)

        report = _run_purge(settings, store, purge_id, hotel_code, created_at)
        store.update_purge_state(purge_id, "done", _stamp(), report, completed=True)
        finished = store.get_purge(purge_id)
        return _finished_payload(finished, store, reused=False)

def _run_purge(settings: config.Settings, store, purge_id: str,
               hotel_code: str, created_at: str) -> dict:
    # 恢复时先读回已提交批次的进度（含清除前账目快照），已完成批次不再重算。
    done_reports = {item["run_id"]: item for item in store.load_purge_progress(purge_id)}
    run_reports = []
    for run_id in store.all_run_ids():
        if run_id in done_reports:
            run_reports.append(done_reports[run_id])
            continue
        item = _purge_one_run(settings, store, purge_id, hotel_code, run_id)
        run_reports.append(item)
        # 该批次事务已提交：立刻落进度，崩溃后从这里继续。
        store.save_purge_progress(purge_id, run_reports)
    residuals = _residual_check(settings, store, hotel_code)
    totals_before = _sum_accounts(run_reports, "before")
    totals_after = _sum_accounts(run_reports, "after")
    voucher_totals = _sum_voucher_columns(run_reports)
    differences = _differences(totals_before, totals_after)
    # 守恒：清除前后差额必须恰好等于凭据承接量，否则任务不算完成。
    variances = _variance_check(differences, voucher_totals)
    report = {
        "purge_id": purge_id,
        "hotel_code": hotel_code,
        "rules_version": PURGE_RULES,
        "created_at": created_at,
        "state": "done",
        "runs": run_reports,
        "totals_before": totals_before,
        "totals_after": totals_after,
        "differences": differences,
        "voucher_totals": voucher_totals,
        "variances": variances,
        "residuals": residuals,
        "balanced": all(item["ok"] for item in variances)
                    and all(item["ok"] for item in residuals),
    }
    if not report["balanced"]:
        raise DomainError("清除后对账不平，已保留凭据待人工复核",
                          code="E_PURGE_UNBALANCED",
                          variances=variances, residuals=residuals)
    return report


def _purge_one_run(settings: config.Settings, store, purge_id: str,
                   hotel_code: str, run_id: str) -> dict:
    run = store.get_run(run_id)
    before = dict(run["summary"].get("accounts", {}))
    hotel_points = store.points_for_hotel(run_id, hotel_code)
    hotel_quarantine = store.quarantine_for_hotel(run_id, hotel_code)
    fresh_point_vouchers = build_point_vouchers(hotel_points, run_id)
    fresh_quarantine_vouchers = build_quarantine_vouchers(hotel_quarantine, run_id)
    for item in fresh_point_vouchers:
        item["purge_id"] = purge_id
    for item in fresh_quarantine_vouchers:
        item["purge_id"] = purge_id

    # 先按“假定删除后”的剩余明细复算账目，再与凭据写入、明细删除放进同一事务。
    # 事务整体回滚时读路径仍看到完整旧账，绝不会读到半清除批次。
    summary, after = _summary_without_hotel(settings, store, run_id,
                                            hotel_code, purge_id)
    txn = store.purge_run_in_transaction(
        run_id, hotel_code, purge_id,
        fresh_point_vouchers, fresh_quarantine_vouchers, summary)

    if txn["skipped"]:
        # 不会发生：进度在事务成功后才落库；保留防御性分支。
        point_vouchers = [item for item in store.load_point_vouchers(purge_id)
                          if item["run_id"] == run_id]
        quarantine_vouchers = [item for item in store.load_quarantine_vouchers(purge_id)
                               if item["run_id"] == run_id]
        removed_points = sum(item["point_count"] for item in point_vouchers)
        removed_quarantine = sum(item["entry_count"] for item in quarantine_vouchers)
    else:
        point_vouchers = fresh_point_vouchers
        quarantine_vouchers = fresh_quarantine_vouchers
        removed_points = txn["removed_points"]
        removed_quarantine = txn["removed_quarantine"]

    return {
        "run_id": run_id,
        "before": before,
        "after": after,
        "removed_points": removed_points,
        "removed_quarantine": removed_quarantine,
        "point_voucher_rows": len(point_vouchers),
        "quarantine_voucher_rows": len(quarantine_vouchers),
        "voucher_totals": voucher_totals(point_vouchers, quarantine_vouchers),
        "checks": summary["checks"],
    }


def _summary_without_hotel(settings: config.Settings, store, run_id: str,
                           hotel_code: str, purge_id: str) -> tuple[dict, dict]:
    """在不改动库的前提下，按内存中剔除该门店后的明细复算 summary。"""
    run = store.get_run(run_id)
    points = [point for point in store.load_points(run_id)
              if point["hotel_code"] != hotel_code]
    entries = [entry for entry in store.load_quarantine(run_id)
               if entry.get("hotel_code") != hotel_code]
    grid = _recompute_grid(points, entries)
    quarantine_rows = [entry for entry in entries
                       if reasons.account(entry["code"]) == "quarantine"]
    superseded_rows = [entry for entry in entries
                       if reasons.account(entry["code"]) == "duplicate"]
    revenue = sum((Decimal(point["revenue_net"]) for point in points), Decimal("0"))
    accounts = {
        "rows_read": grid["points_valid"] + len(superseded_rows) + len(quarantine_rows),
        "rows_accepted": grid["points_valid"],
        "rows_superseded": len(superseded_rows),
        "rows_quarantine": len(quarantine_rows),
        "points_grid": grid["points_grid"],
        "points_valid": grid["points_valid"],
        "points_rejected": grid["points_rejected"],
        "points_missing": grid["points_missing"],
        "revenue_accepted": str(revenue.quantize(settings.money_quant)),
        "late_points": sum(1 for point in points if point["late_flag"]),
        "out_of_window_points": sum(1 for point in points if point["out_of_window"]),
    }
    summary = dict(run["summary"])
    summary["accounts"] = accounts
    new_grid = {k: v for k, v in grid.items()
                if k in ("combos", "days", "start", "end")}
    new_grid["late_days_allowed"] = settings.late_days_allowed
    summary["grid"] = new_grid
    summary["watermark"] = max((point["biz_date"] for point in points), default="")
    summary["reason_counts"] = quarantine_mod.group_counts(entries)
    summary["checks"] = _account_checks(accounts)
    summary["detection"] = detect_service.detect(
        batch_service._points_view(points), settings)["summary"]
    purged = set(summary.get("purged_hotels", []))
    purged.add(hotel_code)
    summary["purged_hotels"] = sorted(purged)
    summary.setdefault("purge_notices", []).append({
        "code": "N_HOTEL_PURGED", "purge_id": purge_id,
        "label": reasons.label("N_HOTEL_PURGED"),
    })
    return summary, accounts


# ------------------------------------------------------------------
# 报告核对：差额、凭据守恒、残留扫描
# ------------------------------------------------------------------
def _sum_accounts(run_reports: list[dict], side: str) -> dict:
    keys = ("rows_read", "rows_accepted", "rows_superseded", "rows_quarantine",
            "points_grid", "points_valid", "points_rejected", "points_missing",
            "late_points", "out_of_window_points")
    totals = {key: 0 for key in keys}
    totals["revenue_accepted"] = "0"
    revenue = Decimal("0")
    for item in run_reports:
        accounts = item[side]
        for key in keys:
            totals[key] += int(accounts.get(key, 0))
        revenue += Decimal(accounts.get("revenue_accepted", "0"))
    totals["revenue_accepted"] = str(revenue)
    return totals


def _sum_voucher_columns(run_reports: list[dict]) -> dict:
    totals = {"points": 0, "rooms_available": 0, "rooms_sold": 0,
              "revenue_net": "0", "quarantine_entries": 0}
    revenue = Decimal("0")
    for item in run_reports:
        voucher = item["voucher_totals"]
        totals["points"] += voucher["points"]
        totals["rooms_available"] += voucher["rooms_available"]
        totals["rooms_sold"] += voucher["rooms_sold"]
        totals["quarantine_entries"] += voucher["quarantine_entries"]
        revenue += Decimal(voucher["revenue_net"])
    totals["revenue_net"] = str(revenue)
    return totals


def _differences(before: dict, after: dict) -> dict:
    integer_keys = ("rows_read", "rows_accepted", "rows_superseded", "rows_quarantine",
                    "points_grid", "points_valid", "points_rejected", "points_missing",
                    "late_points", "out_of_window_points")
    out = {key: before[key] - after[key] for key in integer_keys}
    out["revenue_accepted"] = str(
        Decimal(before["revenue_accepted"]) - Decimal(after["revenue_accepted"]))
    return out


def _variance_check(differences: dict, voucher_totals: dict) -> list[dict]:
    # 隔离凭据同时承接 quarantine 账与 duplicate 账（D_SUPERSEDED 也能定位门店），
    # 行账差额必须等于凭据承接的全部隔离表条目数。
    removed_ledger_rows = (differences["rows_quarantine"]
                           + differences["rows_superseded"])
    checks = [
        ("有效点差额 = 凭据点数", differences["points_valid"], voucher_totals["points"]),
        ("隔离表差额 = 凭据隔离条数（含被覆盖行）", removed_ledger_rows,
         voucher_totals["quarantine_entries"]),
        ("收入差额 = 凭据净收入", Decimal(differences["revenue_accepted"]),
         Decimal(voucher_totals["revenue_net"])),
        ("行账差额 = 采信差额 + 隔离表差额", differences["rows_read"],
         differences["rows_accepted"] + removed_ledger_rows),
    ]
    return [{"name": name, "difference": str(left), "voucher": str(right),
             "ok": Decimal(str(left)) == Decimal(str(right))}
            for name, left, right in checks]


def _residual_check(settings: config.Settings, store, hotel_code: str) -> list[dict]:
    """完成前核对采信、隔离及复盘残留：任何能定位该门店的残留都算失败。"""
    point_hits = 0
    quarantine_hits = 0
    review_hits = 0
    detection_hits = 0
    for run_id in store.all_run_ids():
        point_hits += len(store.points_for_hotel(run_id, hotel_code))
        quarantine_hits += len(store.quarantine_for_hotel(run_id, hotel_code))
        points = batch_service.load_view(store, run_id)
        if points:
            result = detect_service.detect(points, settings)
            detection_hits += sum(1 for item in result["detections"]
                                  if item["hotel_code"] == hotel_code)
            review_hits += sum(1 for event in result["events"]
                               if event["hotel_code"] == hotel_code)
    return [
        {"name": "采信明细残留", "code": "points", "count": point_hits,
         "ok": point_hits == 0},
        {"name": "隔离记录残留", "code": "quarantine", "count": quarantine_hits,
         "ok": quarantine_hits == 0},
        {"name": "异常检测残留", "code": "detection", "count": detection_hits,
         "ok": detection_hits == 0},
        {"name": "复盘事件残留", "code": "review", "count": review_hits,
         "ok": review_hits == 0},
    ]


def _finished_payload(purge: dict, store, reused: bool) -> dict:
    payload = dict(purge["report"])
    payload["state"] = purge["state"]
    payload["reused"] = reused
    payload["point_vouchers"] = store.load_point_vouchers(purge["purge_id"])
    payload["quarantine_vouchers"] = store.load_quarantine_vouchers(purge["purge_id"])
    return payload


def status_payload(store) -> dict:
    running = store.purge_running()
    return {
        "running": running["hotel_code"] if running else "",
        "decommissioned": sorted(store.decommissioned_hotels()),
        "purges": store.list_purges(),
    }
