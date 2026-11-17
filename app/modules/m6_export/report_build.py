"""报告装配：把批次、四账、异常事件、归因分解拼成一份可打印的结论。"""
from __future__ import annotations


def build(run: dict, detection: dict, attribution: dict, reconcile: dict) -> dict:
    summary = run["summary"]
    accounts = summary.get("accounts", {})
    return {
        "run": {
            "run_id": run["run_id"],
            "input_name": run["input_name"],
            "input_sha256": run["input_sha256"],
            "input_bytes": run["manifest"].get("input_bytes", 0),
            "rules_version": run["rules_version"],
            "created_at": run["created_at"],
            "duration_ms": run["duration_ms"],
            "encoding": summary.get("encoding", ""),
            "watermark": summary.get("watermark", ""),
        },
        "accounts": accounts,
        "checks": reconcile.get("checks", []),
        "grid": summary.get("grid", {}),
        "detection": detection.get("summary", {}),
        "events": detection.get("events", [])[:5],
        "attribution": {
            "dim": attribution.get("dim", ""),
            "base": attribution.get("base", []),
            "cur": attribution.get("cur", []),
            "total": attribution.get("total", {}),
            "rows": attribution.get("rows", []),
            "other": attribution.get("other", {}),
            "share_sum": attribution.get("share_sum", ""),
            "coverage": attribution.get("coverage", {}),
            "identity_ok": attribution.get("identity_ok", False),
        },
        "reason_counts": summary.get("reason_counts", []),
        "reconcile_ok": reconcile.get("identity_ok", False),
    }
