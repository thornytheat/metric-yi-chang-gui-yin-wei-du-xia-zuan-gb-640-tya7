"""重复快照归因：同键多条，取最晚快照；同一快照时间给出不同数值判冲突。

不按写入顺序覆盖：排序键是 (快照瞬时, 行号)，顺序与文件行序无关，
同一批数据无论怎么打乱都得到同一结论。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from decimal import Decimal

from app import reasons
from app.modules.m2_calendar.service import CoercedRow

NUMERIC_FIELDS = ("rooms_available", "rooms_sold", "adr", "revenue_net")


@dataclass
class ResolveResult:
    winners: list[CoercedRow] = field(default_factory=list)
    superseded: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)

    def winner_keys(self) -> set:
        return {row.key() for row in self.winners}

    def conflict_keys(self) -> set:
        return {(entry["hotel_code"], entry["channel"], entry["biz_date"]) for entry in self.conflicts}


def _values(row: CoercedRow) -> tuple:
    return tuple(getattr(row, name) for name in NUMERIC_FIELDS)


def _snapshot_entry(row: CoercedRow, winner: CoercedRow | None, code: str, detail: str) -> dict:
    entry = {
        "line_no": row.line_no,
        "code": code,
        "label": reasons.label(code),
        "field": "snapshot",
        "raw": f"{row.hotel_code}|{row.channel}|{row.biz_date}|{row.revenue_net}",
        "detail": detail,
        "hotel_code": row.hotel_code,
        "channel": row.channel,
        "biz_date": row.biz_date.isoformat(),
        "snapshot_utc": row.snapshot_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "revenue_net": str(row.revenue_net),
    }
    if winner is not None:
        entry["winner_line"] = winner.line_no
        entry["winner_snapshot_utc"] = winner.snapshot_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    return entry


def resolve(rows: list[CoercedRow]) -> ResolveResult:
    groups: dict[tuple, list[CoercedRow]] = {}
    for row in rows:
        groups.setdefault(row.key(), []).append(row)
    result = ResolveResult()
    for key in sorted(groups):
        group = sorted(groups[key], key=lambda item: (item.snapshot_utc, item.line_no))
        if len(group) == 1:
            result.winners.append(group[0])
            continue
        stamps: dict[str, list[CoercedRow]] = {}
        for row in group:
            stamps.setdefault(row.snapshot_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), []).append(row)
        conflict = any(len({_values(row) for row in bucket}) > 1 for bucket in stamps.values())
        if conflict:
            detail = "同键同一快照时间给出不同数值，整键隔离，不猜哪条为准"
            for row in group:
                result.conflicts.append(_snapshot_entry(row, None, "Q_DUP_CONFLICT", detail))
            continue
        winner = group[-1]
        result.winners.append(winner)
        for row in group[:-1]:
            result.superseded.append(_snapshot_entry(
                row, winner, "D_SUPERSEDED",
                f"同键存在更晚快照（{winner.snapshot_utc:%H:%M} 行 {winner.line_no}）"))
    result.winners.sort(key=lambda item: (item.hotel_code, item.channel, item.biz_date))
    return result


def naive_last_write_wins(rows: list[CoercedRow]) -> list[CoercedRow]:
    """反例对照：按文件行序后写覆盖，冲突键会被悄悄保留一条。"""
    latest: dict[tuple, CoercedRow] = {}
    for row in rows:
        latest[row.key()] = row
    return list(latest.values())


def sum_revenue(rows: list[CoercedRow]) -> Decimal:
    return sum((row.revenue_net for row in rows), Decimal("0"))
