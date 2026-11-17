"""隔离区：条目结构、原因分布、可导出 CSV（原值 + 原因码 + 行号）。"""
from __future__ import annotations

import csv
import io

from app import reasons

COLUMNS = ("line_no", "code", "label", "field", "raw", "detail",
           "hotel_code", "channel", "biz_date", "snapshot_utc", "winner_line")


def build(entries: list[dict]) -> list[dict]:
    ordered = sorted(entries, key=lambda e: (int(e.get("line_no", 0)), e.get("code", "")))
    for entry in ordered:
        entry.setdefault("label", reasons.label(entry.get("code", "")))
        entry.setdefault("account", reasons.account(entry.get("code", "")))
    return ordered


def group_counts(entries: list[dict]) -> list[dict]:
    tally: dict[str, int] = {}
    for entry in entries:
        tally[entry["code"]] = tally.get(entry["code"], 0) + 1
    rows = [{"code": code, "label": reasons.label(code),
             "account": reasons.account(code), "count": count}
            for code, count in tally.items()]
    rows.sort(key=lambda item: (-item["count"], item["code"]))
    return rows


def to_csv(entries: list[dict]) -> str:
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for entry in entries:
        writer.writerow([entry.get(column, "") for column in COLUMNS])
    return buffer.getvalue()
