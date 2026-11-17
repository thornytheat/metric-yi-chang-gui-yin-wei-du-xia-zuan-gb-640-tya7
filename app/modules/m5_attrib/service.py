"""批次编排与归因编排。

run_batch：采信 → 归一 → 质量 → 清单/落库（同源重跑直接复用批次）。
attribution：按半开区间选基期与当期，组内 LMDI 分解 + 维度份额。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app import config, manifest, reasons
from app.modules.m1_ingest import service as ingest_service
from app.modules.m2_calendar import service as coerce_service, tz
from app.modules.m3_quality import service as quality_service
from app.modules.m4_detect import service as detect_service
from app.modules.m5_attrib import batches, dimension, lmdi


def _summary(run_manifest: manifest.RunManifest, quality, ingest, detection: dict,
             settings: config.Settings) -> dict:
    accounts = quality.accounts
    return {
        "run_id": run_manifest.run_id,
        "input_name": run_manifest.input_name,
        "input_sha256": run_manifest.input_sha256,
        "input_bytes": run_manifest.input_bytes,
        "encoding": run_manifest.encoding,
        "rules_version": run_manifest.rules_version,
        "params_key": run_manifest.params_key,
        "created_at": run_manifest.created_at,
        "duration_ms": run_manifest.duration_ms,
        "counts": run_manifest.counts,
        "accounts": accounts,
        "checks": quality_service.account_checks(accounts),
        "grid": quality.grid,
        "watermark": quality.watermark,
        "reason_counts": quality.reason_counts,
        "notices": ingest.notices,
        "late_marks": quality.late_marks[:50],
        "duplicates": quality.superseded[:50],
        "conflicts": quality.conflicts[:50],
        "detection": detection["summary"],
        "header": ingest.header,
        "unknown_columns": ingest.unknown_columns,
        "duplicated_columns": ingest.duplicated_columns,
        "empty_state": ingest.empty_state,
        "params": settings.params_digest_payload(),
    }


def run_batch(settings: config.Settings, store, data: bytes, input_name: str) -> dict:
    # M8：清除优先于导入。即使命中同源批次也要先拒绝，保证清除期间没有任何写入口。
    running_purge = store.purge_running()
    if running_purge is not None:
        from app.errors import Conflict
        raise Conflict(
            "门店退网清除进行中（%s），导入需等清除完成" % running_purge["hotel_code"],
            code="E_PURGE_RUNNING", purge_id=running_purge["purge_id"],
            hotel_code=running_purge["hotel_code"])
    digest = manifest.sha256_bytes(data)
    params_key = manifest.params_digest(settings.params_digest_payload())
    key = batches.run_key(digest, settings.rules_version, params_key)
    existing_id = store.run_id_for_key(key)
    if existing_id:
        run = store.get_run(existing_id)
        payload = dict(run["summary"])
        payload["reused"] = True
        payload["run_key"] = key
        return payload

    with store.write_lock:
        # 拿锁后复查：清除任务可能恰好在上面检查之后抢先持锁。
        running_purge = store.purge_running()
        if running_purge is not None:
            from app.errors import Conflict
            raise Conflict(
                "门店退网清除进行中（%s），导入需等清除完成" % running_purge["hotel_code"],
                code="E_PURGE_RUNNING", purge_id=running_purge["purge_id"],
                hotel_code=running_purge["hotel_code"])
        return _build_batch(settings, store, data, input_name, digest, params_key, key)


def _build_batch(settings: config.Settings, store, data: bytes, input_name: str,
                 digest: str, params_key: str, key: str) -> dict:
    clock = manifest.RunClock()
    ingest = ingest_service.ingest(data, input_name, settings)
    # M8：退网门店即使随同文件重导也不恢复，采信阶段就剔除其全部行。
    decommissioned = store.decommissioned_hotels()
    if decommissioned:
        kept_rows = [row for row in ingest.rows
                     if (row.get("hotel_code") or "").strip() not in decommissioned]
        skipped = len(ingest.rows) - len(kept_rows)
        ingest.rows = kept_rows
        if skipped:
            ingest.notices = list(ingest.notices) + [{
                "code": "N_HOTEL_PURGED",
                "label": reasons.label("N_HOTEL_PURGED"),
                "detail": "退网门店 %d 行在采信阶段剔除，不恢复明细" % skipped,
                "skipped_rows": skipped,
                "hotels": sorted(decommissioned),
            }]
    coerced, coerce_rejects = coerce_service.coerce_rows(ingest.rows, settings)
    if decommissioned:
        coerce_rejects = [item for item in coerce_rejects
                          if item.get("hotel_code") not in decommissioned]
    quality = quality_service.evaluate(
        coerced, list(ingest.rejects) + coerce_rejects, settings,
        records_read=ingest.total_records)
    points = quality.points
    detection = detect_service.detect(_points_view(points), settings)
    run_id = manifest.make_run_id(digest, settings.rules_version, params_key)
    created_at = tz.local_stamp(dt.datetime.now(dt.timezone.utc))
    run_manifest = manifest.RunManifest(
        run_id=run_id,
        input_name=input_name,
        input_sha256=digest,
        input_bytes=len(data),
        rules_version=settings.rules_version,
        params_key=params_key,
        created_at=created_at,
        duration_ms=clock.elapsed_ms(),
        counts={
            "rows_read": ingest.total_records,
            "rows_accepted": len(points),
            "rows_quarantine": quality.accounts["rows_quarantine"],
            "rows_superseded": quality.accounts["rows_superseded"],
            "points_grid": quality.accounts["points_grid"],
            "points_missing": quality.accounts["points_missing"],
        },
        encoding=ingest.encoding,
        empty_state="",
    )
    summary = _summary(run_manifest, quality, ingest, detection, settings)
    summary["run_key"] = key
    summary["reused"] = False
    if decommissioned:
        summary["purged_hotels"] = sorted(decommissioned)
    store.save_run(run_manifest.as_dict(), key, summary)
    store.replace_points(run_id, points)
    store.replace_quarantine(run_id, quality.quarantine)
    return summary


def _points_view(points: list[dict]) -> list[dict]:
    """落库点是字符串形态，检测与归因需要 Decimal，统一在这里转一次。"""
    out = []
    for point in points:
        item = dict(point)
        item["revenue_net"] = Decimal(item["revenue_net"])
        item["adr"] = Decimal(item["adr"])
        out.append(item)
    return out


def load_view(store, run_id: str) -> list[dict]:
    return _points_view(store.load_points(run_id))


def windows(watermark: str, cur_days: int, base_days: int) -> dict:
    end = dt.date.fromisoformat(watermark) + dt.timedelta(days=1)
    cur_start = end - dt.timedelta(days=cur_days)
    base_end = cur_start
    base_start = base_end - dt.timedelta(days=base_days)
    return {
        "base": (base_start, base_end),
        "cur": (cur_start, end),
        "base_text": [base_start.isoformat(), base_end.isoformat()],
        "cur_text": [cur_start.isoformat(), end.isoformat()],
    }


def attribution(settings: config.Settings, store, run_id: str | None,
                dim: str = "hotel_code", cur_days: int = 7, base_days: int = 7) -> dict:
    run = store.get_run(run_id) if run_id else store.latest_run()
    if run is None:
        return {}
    points = load_view(store, run["run_id"])
    watermark = batches.watermark_of(points)
    if not watermark:
        return {"run_id": run["run_id"], "empty": True}
    window = windows(watermark, max(1, cur_days), max(1, base_days))
    rows = dimension.group_rows(points, window["base"], window["cur"], dim, settings)
    share_info = dimension.shares(rows, settings)

    base_all = dimension.aggregate(points, window["base"][0], window["base"][1], dim)
    cur_all = dimension.aggregate(points, window["cur"][0], window["cur"][1], dim)
    base_factors = _total_factors(base_all)
    cur_factors = _total_factors(cur_all)
    total_part = lmdi.decompose(base_factors["rev"], cur_factors["rev"],
                                base_factors, cur_factors, settings.money_quant)
    parts = [lmdi.decompose(row["decomposition"]["rev_base"],
                            row["decomposition"]["rev_cur"],
                            _row_factors(row, "base"), _row_factors(row, "cur"))
             for row in rows]
    coverage = {
        "series_base": len(base_all),
        "series_cur": len(cur_all),
        "series_both": len(set(base_all) & set(cur_all)),
        "series_only_base": len(set(base_all) - set(cur_all)),
        "series_only_cur": len(set(cur_all) - set(base_all)),
        "points_base": sum(1 for point in points
                           if window["base"][0] <= dt.date.fromisoformat(point["biz_date"])
                           < window["base"][1]),
        "points_cur": sum(1 for point in points
                          if window["cur"][0] <= dt.date.fromisoformat(point["biz_date"])
                          < window["cur"][1]),
    }
    return {
        "run_id": run["run_id"],
        "watermark": watermark,
        "dim": dim,
        "base": window["base_text"],
        "cur": window["cur_text"],
        "total": total_part.as_dict(settings.money_quant),
        "rows": share_info["rows"],
        "other": share_info["other"],
        "share_sum": share_info["share_sum"],
        "zero_total": share_info["zero_total"],
        "coverage": coverage,
        "identity_ok": lmdi.identity_holds(parts + [total_part], settings.money_quant),
        "rules_version": run["rules_version"],
    }


def _total_factors(groups: dict[str, dict]) -> dict:
    merged = {"rev": Decimal("0"), "avail": Decimal("0"), "sold": Decimal("0")}
    for bucket in groups.values():
        for key in merged:
            merged[key] += bucket[key]
    return dimension.factors_of(merged)


def _row_factors(row: dict, side: str) -> dict:
    avail = Decimal(row[f"avail_{side}"])
    sold = Decimal(row[f"sold_{side}"])
    rev = Decimal(row["rev_base"] if side == "base" else row["rev_cur"])
    return {
        "rooms": avail,
        "occ": (sold / avail) if avail > 0 else Decimal("0"),
        "adr": (rev / sold) if sold > 0 else Decimal("0"),
        "rev": rev,
    }


def reconcile(settings: config.Settings, store, run_id: str | None) -> dict:
    run = store.get_run(run_id) if run_id else store.latest_run()
    if run is None:
        return {}
    summary = run["summary"]
    accounts = summary.get("accounts", {})
    checks = quality_service.account_checks(accounts)
    decomposition = {
        "name": "分解账",
        "formula": "净收入差 = 三因子之和 + 残差",
        "detail": "见归因页；基期与当期因子为 0 时整组记入残差并标注",
        "ok": True,
    }
    known_codes = {row["code"] for row in summary.get("reason_counts", [])}
    unknown = [code for code in known_codes if code not in reasons.REASONS]
    return {
        "run_id": run["run_id"],
        "accounts": accounts,
        "checks": checks,
        "decomposition": decomposition,
        "watermark": summary.get("watermark", ""),
        "grid": summary.get("grid", {}),
        "reason_counts": summary.get("reason_counts", []),
        "unknown_reason_codes": unknown,
        "identity_ok": all(item["ok"] for item in checks),
    }
