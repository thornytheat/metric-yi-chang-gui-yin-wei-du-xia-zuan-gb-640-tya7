"""运行清单与报告 JSON：任何结论都能被第三方按清单复算。"""
from __future__ import annotations

import json


def manifest_json(run: dict) -> str:
    payload = {
        "run_id": run["run_id"],
        "manifest": run["manifest"],
        "accounts": run["summary"].get("accounts", {}),
        "checks": run["summary"].get("checks", []),
        "watermark": run["summary"].get("watermark", ""),
        "rules_version": run["rules_version"],
        "reason_counts": run["summary"].get("reason_counts", []),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
