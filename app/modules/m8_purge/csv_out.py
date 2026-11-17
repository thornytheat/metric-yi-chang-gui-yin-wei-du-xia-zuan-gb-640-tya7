"""退网汇总凭据 CSV：只含 run × 渠道 × 业务日聚合，不含任何门店标识。

财务复核旧批次四账时用凭据承接原账；导出列里刻意没有 hotel_code/hotel_name，
文件名也只带 purge_id，避免凭据本身成为定位退网门店的线索。
"""
from __future__ import annotations

import csv
import io

POINT_VOUCHER_COLUMNS = ("run_id", "channel", "biz_date", "point_count",
                         "rooms_available", "rooms_sold", "revenue_net")
QUARANTINE_VOUCHER_COLUMNS = ("run_id", "code", "entry_count")


def _dump(header: tuple, rows: list[list]) -> str:
    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def point_vouchers_csv(vouchers: list[dict]) -> str:
    return _dump(POINT_VOUCHER_COLUMNS,
                 [[item.get(column, "") for column in POINT_VOUCHER_COLUMNS]
                  for item in vouchers])


def quarantine_vouchers_csv(vouchers: list[dict]) -> str:
    return _dump(QUARANTINE_VOUCHER_COLUMNS,
                 [[item.get(column, "") for column in QUARANTINE_VOUCHER_COLUMNS]
                  for item in vouchers])
