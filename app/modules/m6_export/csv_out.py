"""证据行导出：指标点、异常点、隔离行、归因表。

每行都带行号与原因码，导出的数能与页面逐条对上。
"""
from __future__ import annotations

import csv
import io

POINT_COLUMNS = ("biz_date", "hotel_code", "hotel_name", "channel", "stratum",
                 "rooms_available", "rooms_sold", "adr", "revenue_net",
                 "late_flag", "source_line", "updated_utc")
ANOMALY_COLUMNS = ("biz_date", "hotel_code", "channel", "stratum", "value", "median",
                   "dispersion", "z", "method", "status", "direction", "baseline_points")
ATTRIB_COLUMNS = ("key", "label", "rev_base", "rev_cur", "delta", "share",
                  "rooms_delta", "occ_delta", "adr_delta", "ok")


def _dump(header: tuple, rows: list[list]) -> str:
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def points_csv(points: list[dict]) -> str:
    return _dump(POINT_COLUMNS, [[point.get(column, "") for column in POINT_COLUMNS]
                                 for point in points])


def anomalies_csv(detections: list[dict]) -> str:
    return _dump(ANOMALY_COLUMNS, [[item.get(column, "") for column in ANOMALY_COLUMNS]
                                   for item in detections])


def attribution_csv(result: dict) -> str:
    rows = []
    for row in result.get("rows", []):
        factors = {item["key"]: item["value"] for item in row["decomposition"]["factors"]}
        rows.append([row["key"], row["label"], row["rev_base"], row["rev_cur"],
                     row["delta"], row.get("share", ""), factors.get("rooms", ""),
                     factors.get("occ", ""), factors.get("adr", ""),
                     "平" if row["ok"] else "残差"])
    return _dump(ATTRIB_COLUMNS, rows)
