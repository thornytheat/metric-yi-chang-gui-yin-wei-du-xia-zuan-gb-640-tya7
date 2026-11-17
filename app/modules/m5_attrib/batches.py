"""批次键、历史与续跑：同源同参数重跑必然命中同一个 run_id。

水位：本批有效业务日的最大值；下一批只处理水位之后的数据。
"""
from __future__ import annotations

from app import manifest, reasons


def run_key(input_sha256: str, rules_version: str, params_key: str) -> str:
    return f"{input_sha256}|{rules_version}|{params_key}"


def watermark_of(points: list[dict]) -> str:
    if not points:
        return ""
    return max(point["biz_date"] for point in points)


def history(runs: list[dict], limit: int = 20) -> list[dict]:
    out = []
    for run in runs[:limit]:
        summary = run["summary"]
        out.append({
            "run_id": run["run_id"],
            "created_at": run["created_at"],
            "input_name": run["input_name"],
            "input_sha256": run["input_sha256"][:16],
            "rules_version": run["rules_version"],
            "duration_ms": run["duration_ms"],
            "empty_state": run["empty_state"],
            "points_valid": summary.get("accounts", {}).get("points_valid", 0),
            "rows_quarantine": summary.get("accounts", {}).get("rows_quarantine", 0),
            "watermark": summary.get("watermark", ""),
        })
    return out


def next_steps(existing: dict | None) -> dict:
    """命中已有批次时给出明确结论：复用结果、不新增行。"""
    if existing is None:
        return {"mode": "create", "run_id": "", "note": "新建批次"}
    return {
        "mode": "reuse",
        "run_id": existing["run_id"],
        "note": "同源同参数，复用已有批次",
        "points_valid": existing["summary"].get("accounts", {}).get("points_valid", 0),
    }


def key_parts(run_key_value: str) -> dict:
    parts = run_key_value.split("|")
    if len(parts) != 3:
        return {"input_sha256": run_key_value, "rules_version": "", "params_key": ""}
    return {"input_sha256": parts[0], "rules_version": parts[1], "params_key": parts[2]}


def account_notes(accounts: dict) -> list[dict]:
    return [
        {"code": "D_SUPERSEDED", "label": reasons.label("D_SUPERSEDED"),
         "count": accounts.get("rows_superseded", 0)},
        {"code": "Q_QUARANTINE", "label": "隔离行",
         "count": accounts.get("rows_quarantine", 0)},
        {"code": "L_LATE_ARRIVAL", "label": reasons.label("L_LATE_ARRIVAL"),
         "count": accounts.get("late_points", 0)},
    ]


def digest_for(input_sha256: str, rules_version: str, params_key: str) -> str:
    return manifest.params_digest({"sha": input_sha256, "rules": rules_version,
                                   "params": params_key})
