"""维度贡献下钻：组内净收入差、份额、前 N 组与「其他」守恒。

份额用 4 位小数；「其他」= 1 − 前 N 组份额之和，保证合计恒为 1，
不把各自四舍五入的尾差丢给用户。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app import config
from app.modules.m5_attrib import lmdi


def _empty() -> dict:
    return {"rev": Decimal("0"), "avail": Decimal("0"), "sold": Decimal("0")}


def aggregate(points: list[dict], start: dt.date, end: dt.date, dim: str) -> dict[str, dict]:
    groups: dict[str, dict] = {}
    for point in points:
        day = dt.date.fromisoformat(point["biz_date"])
        if not (start <= day < end):
            continue
        key = point[dim] if dim in ("hotel_code", "channel") else point["hotel_code"]
        bucket = groups.setdefault(key, _empty())
        bucket["rev"] += Decimal(point["revenue_net"])
        bucket["avail"] += Decimal(int(point["rooms_available"]))
        bucket["sold"] += Decimal(int(point["rooms_sold"]))
    return groups


def factors_of(bucket: dict) -> dict:
    avail = bucket["avail"]
    sold = bucket["sold"]
    rev = bucket["rev"]
    occ = (sold / avail) if avail > 0 else Decimal("0")
    adr = (rev / sold) if sold > 0 else Decimal("0")
    return {"rooms": avail, "occ": occ, "adr": adr, "rev": rev}


def group_rows(points: list[dict], base: tuple[dt.date, dt.date],
               cur: tuple[dt.date, dt.date], dim: str, settings: config.Settings) -> list[dict]:
    base_groups = aggregate(points, base[0], base[1], dim)
    cur_groups = aggregate(points, cur[0], cur[1], dim)
    rows = []
    for key in sorted(set(base_groups) | set(cur_groups)):
        base_bucket = base_groups.get(key, _empty())
        cur_bucket = cur_groups.get(key, _empty())
        base_f = factors_of(base_bucket)
        cur_f = factors_of(cur_bucket)
        part = lmdi.decompose(base_f["rev"], cur_f["rev"], base_f, cur_f)
        rows.append({
            "key": key,
            "label": settings_channel_label(key) if dim == "channel" else key,
            "rev_base": base_f["rev"].quantize(settings.money_quant),
            "rev_cur": cur_f["rev"].quantize(settings.money_quant),
            "delta": part.delta.quantize(settings.money_quant),
            "decomposition": part.as_dict(settings.money_quant),
            "avail_base": int(base_bucket["avail"]),
            "avail_cur": int(cur_bucket["avail"]),
            "sold_base": int(base_bucket["sold"]),
            "sold_cur": int(cur_bucket["sold"]),
            "ok": part.ok,
        })
    return rows


def settings_channel_label(key: str) -> str:
    return config.CHANNEL_LABELS.get(key, key)


def shares(rows: list[dict], settings: config.Settings) -> dict:
    """份额守恒：有「其他」时由它吸收尾差，没有「其他」时把尾差记到最大组。"""
    total = sum((row["delta"] for row in rows), Decimal("0"))
    ordered = sorted(rows, key=lambda row: (-abs(row["delta"]), row["key"]))
    top = ordered[: settings.top_n]
    rest = ordered[settings.top_n:]
    zero_total = total == 0
    out = []
    used = Decimal("0")
    for row in top:
        share = (row["delta"] / total).quantize(settings.dim_quant) if not zero_total else None
        if share is not None:
            used += share
        out.append({**row, "share": None if share is None else str(share)})
    rest_delta = sum((row["delta"] for row in rest), Decimal("0"))
    other_share = None
    if rest:
        other_share = (Decimal("1") - used).quantize(settings.dim_quant) if not zero_total else None
        used += other_share if other_share is not None else Decimal("0")
    elif out and not zero_total:
        residual = (Decimal("1") - used).quantize(settings.dim_quant)
        if residual != 0:
            target = max(out, key=lambda row: abs(row["delta"]))
            adjust = Decimal(target["share"]) + residual
            target["share"] = str(adjust.quantize(settings.dim_quant))
            used += residual
    return {
        "total_delta": str(total.quantize(settings.money_quant)),
        "rows": out,
        "other": {
            "count": len(rest),
            "delta": str(rest_delta.quantize(settings.money_quant)),
            "share": None if other_share is None else str(other_share),
        },
        "share_sum": "-" if zero_total else str(used.quantize(settings.dim_quant)),
        "zero_total": zero_total,
    }
