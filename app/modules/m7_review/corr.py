"""同批异常：同一天、同方向、不同门店的确认点归为一组。

朴素做法会把「同一序列的多天」当成多组，或把方向相反的相邻门店凑成一组，
组数与门店数都会虚高。这里按业务日分组、要求方向一致且门店互不相同，
只有连续业务日里门店集合与渠道集合都不变，才合并成同一组。

peers 给单张复盘卡用：与事件区间重叠、同方向的其它序列，
按「同门店不同渠道」与「同渠道不同门店」两种关系分开，不混为一谈。
"""
from __future__ import annotations

import datetime as dt

SCOPE_CHANNEL = "channel"
SCOPE_CHAIN = "chain"
RELATION_HOTEL = "same_hotel"
RELATION_CHANNEL = "same_channel"
RELATION_CHAIN = "chain"


def _scope(items: list[dict]) -> str:
    channels = {item.get("channel") for item in items}
    return SCOPE_CHANNEL if len(channels) == 1 else SCOPE_CHAIN


def daily_clusters(detections: list[dict]) -> list[dict]:
    """业务日 × 方向；同向且门店互不相同（≥2 家）才算一组。"""
    buckets: dict[tuple, list[dict]] = {}
    for item in detections:
        if item.get("status") != "confirmed":
            continue
        buckets.setdefault((item["biz_date"], item["direction"]), []).append(item)
    out: list[dict] = []
    for (day, direction) in sorted(buckets):
        items = buckets[(day, direction)]
        hotels = {item["hotel_code"] for item in items}
        if len(hotels) < 2:
            continue
        out.append({
            "biz_date": day,
            "direction": direction,
            "hotels": sorted(hotels),
            "channels": sorted({item["channel"] for item in items}),
            "series": sorted({item["series"] for item in items}),
            "scope": _scope(items),
            "days": 1,
        })
    return out


def clusters(detections: list[dict]) -> list[dict]:
    """把连续业务日、同向、门店与渠道集合都不变的组合并成一组。"""
    daily = daily_clusters(detections)
    merged: list[dict] = []
    for row in daily:
        if merged:
            last = merged[-1]
            same_scope = (last["direction"] == row["direction"]
                          and last["hotels"] == row["hotels"]
                          and last["channels"] == row["channels"])
            adjacent = _next_day(last["end"]) == row["biz_date"]
            if same_scope and adjacent:
                last["end"] = row["biz_date"]
                last["days"] += 1
                continue
        merged.append({**row, "start": row["biz_date"], "end": row["biz_date"]})
    for row in merged:
        row["hotel_count"] = len(row["hotels"])
        row["group_id"] = "GRP-%s-%s" % (row["start"].replace("-", ""),
                                         "-".join(row["hotels"]))
    return merged


def _next_day(day: str) -> str:
    return (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()


def relation(left: dict, right: dict) -> str:
    if left["hotel_code"] == right["hotel_code"]:
        return RELATION_HOTEL
    if left["channel"] == right["channel"]:
        return RELATION_CHANNEL
    return RELATION_CHAIN


def peers(event: dict, detections: list[dict]) -> list[dict]:
    """与事件区间重叠、同方向、序列不同的确认点，按序列聚合成对照行。"""
    start, end = event["start"], event["end"]
    buckets: dict[str, dict] = {}
    for item in detections:
        if item.get("status") != "confirmed":
            continue
        if item.get("series") == event["series"]:
            continue
        if item.get("direction") != event["direction"]:
            continue
        if not (start <= item.get("biz_date", "") <= end):
            continue
        bucket = buckets.setdefault(item["series"], {
            "series": item["series"],
            "hotel_code": item["hotel_code"],
            "channel": item["channel"],
            "relation": relation(event, item),
            "days": 0,
        })
        bucket["days"] += 1
    return sorted(buckets.values(), key=lambda row: (-row["days"], row["series"]))


def group_of(event: dict, groups: list[dict]) -> dict | None:
    """事件属于哪一组同批异常；跨组只认完整覆盖的那组。"""
    for group in groups:
        if group["direction"] != event["direction"]:
            continue
        if group["start"] <= event["start"] and event["end"] <= group["end"]:
            return group
    return None


def count_hotels(groups: list[dict]) -> int:
    hotels: set[str] = set()
    for group in groups:
        hotels.update(group["hotels"])
    return len(hotels)
