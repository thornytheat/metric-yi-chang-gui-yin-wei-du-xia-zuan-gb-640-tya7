"""M5 归因与批次：LMDI 三因子、残差、份额守恒、半开窗口、幂等重跑。"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app import config
from app.modules.m5_attrib import batches, dimension, lmdi
from app.modules.m5_attrib import service as batch_service
from tests.fixtures import expected as fx

SETTINGS = config.Settings(db_path="/tmp/unused.sqlite", seed_on_start=False)


def _bucket(rev, avail, sold):
    return {"rev": Decimal(rev), "avail": Decimal(avail), "sold": Decimal(sold)}


def test_lmdi_residual_is_explicit_zero(attribution):
    # TRAP-24：三因子 + 残差 = 差额，残差显式给出且量化到分
    total = attribution["total"]
    parts = sum(Decimal(item["value"]) for item in total["factors"])
    residual = Decimal(total["residual"])
    assert residual == Decimal("0.00")
    assert parts + residual == Decimal(total["delta"])
    assert attribution["identity_ok"] is True


def test_lmdi_factors_reconstruct_delta():
    base = _bucket("100000", "1000", "700")
    cur = _bucket("120000", "1100", "790")
    base_f = dimension.factors_of(base)
    cur_f = dimension.factors_of(cur)
    part = lmdi.decompose(base_f["rev"], cur_f["rev"], base_f, cur_f)
    assert part.ok is True
    assert part.factor_sum().quantize(Decimal("0.01")) + part.residual == part.delta.quantize(
        Decimal("0.01"))
    assert len(part.factors) == 3


def test_lmdi_zero_base_factor_marked():
    base = _bucket("0", "0", "0")
    cur = _bucket("12000", "100", "60")
    base_f = dimension.factors_of(base)
    cur_f = dimension.factors_of(cur)
    part = lmdi.decompose(base_f["rev"], cur_f["rev"], base_f, cur_f)
    assert part.ok is False
    assert part.code == "A_ZERO_BASE"
    assert part.residual == part.delta.quantize(Decimal("0.01"))


def test_dimension_shares_conserve_to_one(attribution):
    # TRAP-24：份额按 4 位小数收敛，合计必须正好 1.0000
    assert attribution["share_sum"] == "1.0000"
    total_share = sum(Decimal(row["share"]) for row in attribution["rows"])
    if attribution["other"]["share"]:
        total_share += Decimal(attribution["other"]["share"])
    assert total_share == Decimal("1.0000")


def test_top_n_merges_rest_into_other():
    rows = [{"key": f"K{index}", "label": f"K{index}", "delta": Decimal(index * 100)}
            for index in range(1, 8)]
    for row in rows:
        row["decomposition"] = {"factors": [], "rev_base": "0", "rev_cur": "0"}
    result = dimension.shares(rows, SETTINGS)
    assert len(result["rows"]) == 5
    assert result["other"]["count"] == 2
    assert result["share_sum"] == "1.0000"


def test_windows_are_half_open_and_touch():
    # TRAP-23：基期与当期首尾相接，不重不漏
    window = batch_service.windows(fx.WATERMARK, 7, 7)
    assert window["base_text"][1] == window["cur_text"][0]
    assert window["cur_text"][1] == "2026-11-22"
    base_days = set(range(7))
    base_start = dt.date.fromisoformat(window["base_text"][0])
    cur_start = dt.date.fromisoformat(window["cur_text"][0])
    assert (cur_start - base_start).days == 7
    assert len(base_days) == 7


def test_attribution_window_labels(attribution):
    assert attribution["base"] == ["2026-11-08", "2026-11-15"]
    assert attribution["cur"] == ["2026-11-15", "2026-11-22"]
    assert attribution["watermark"] == fx.WATERMARK
    assert attribution["coverage"]["series_both"] == 4  # 按门店维度只有 4 组


def test_rerun_is_idempotent(settings, store, dirty_bytes, run):
    # 不变量：同源同参数重跑命中同一批次，行数与合计都不变
    again = batch_service.run_batch(settings, store, dirty_bytes, "dirty.csv")
    assert again["run_id"] == run["run_id"]
    assert again["reused"] is True
    assert again["accounts"] == run["accounts"]
    assert store.point_count(run["run_id"]) == run["accounts"]["points_valid"]


def test_run_key_depends_on_all_parts(settings):
    key = batches.run_key("sha", "rules", "params")
    parts = batches.key_parts(key)
    assert parts["input_sha256"] == "sha"
    assert parts["rules_version"] == "rules"
    assert parts["params_key"] == "params"
    other = batches.run_key("sha", "rules", "params2")
    assert other != key


def test_manifest_records_sha_and_rules(run, store):
    stored = store.get_run(run["run_id"])
    assert stored["manifest"]["input_sha256"] == run["input_sha256"]
    assert stored["manifest"]["rules_version"] == config.RULES_VERSION
    assert stored["manifest"]["input_bytes"] > 80000
    assert stored["manifest"]["encoding"] == "utf-8-sig"


def test_batches_history_lists_run(store, run):
    history = batches.history(store.list_runs(), 5)
    assert history[0]["run_id"] == run["run_id"]
    assert history[0]["points_valid"] == run["accounts"]["points_valid"]
    assert len(history[0]["input_sha256"]) == 16


def test_attribution_api_rejects_bad_dimension(client):
    bad_dim = client.get("/api/attribution?dimension=city")
    assert bad_dim.status_code == 400
    assert bad_dim.json()["error"] == "E_BAD_DIMENSION"
    bad_window = client.get("/api/attribution?cur_days=0")
    assert bad_window.status_code == 400
    assert bad_window.json()["error"] == "E_BAD_WINDOW"


def test_batches_api_and_detail(client, run):
    listing = client.get("/api/batches").json()
    assert listing["latest"] == run["run_id"]
    detail = client.get(f"/api/batches/{run['run_id']}").json()
    assert detail["manifest"]["run_id"] == run["run_id"]
    assert detail["key_parts"]["rules_version"] == config.RULES_VERSION
    missing = client.get("/api/batches/run-nope")
    assert missing.status_code == 404


def test_zero_total_delta_marks_share_none():
    # TRAP-24 边界：总差额为 0 时不除零，份额整体记空并显式标 zero_total
    rows = [{"key": "H01", "delta": Decimal("0")},
            {"key": "H02", "delta": Decimal("0")}]
    result = dimension.shares(rows, SETTINGS)
    assert result["zero_total"] is True
    assert result["share_sum"] == "-"
    assert all(row["share"] is None for row in result["rows"])
    assert result["other"]["share"] is None
