"""复盘卡导出：一行一张卡，带基线、复核 z、置信与标记码。

页面上的每一列都能在导出里对上，标记码用 R_* 稳定码，不写自然语言。
"""
from __future__ import annotations

import csv
import io

CARD_COLUMNS = ("event_id", "series", "start", "end", "days", "direction",
                "delta", "peak_z", "stratum", "confidence", "verdict",
                "baseline_points", "holiday_points", "method", "baseline_median",
                "review_z", "dominant", "flags", "flipped", "group_id")


def _row(card: dict) -> list:
    base = card["baseline"]
    factors = card["factors"]
    return [
        card["event_id"],
        card["series"],
        card["start"],
        card["end"],
        card["days"],
        card["direction"],
        card["delta"],
        card["peak_z"],
        card["stratum"],
        card["confidence"],
        card["verdict"],
        base["points"],
        base["holiday_points"],
        base["method"],
        base["median"],
        base["z"] or "",
        factors.get("dominant") or factors.get("code", ""),
        "|".join(flag["code"] for flag in card["flags"]),
        "|".join(card["flipped"]),
        card["group_id"],
    ]


def cards_csv(cards: list[dict]) -> str:
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CARD_COLUMNS)
    writer.writerows([_row(card) for card in cards])
    return buffer.getvalue()
