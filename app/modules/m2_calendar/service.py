"""值归一：全角转半角、去千分位、剥单位、哨兵三态、日期与快照归一。

哨兵一律返回 None 并单独成原因码，绝不落成 0 参与合计。
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app import config, reasons
from app.modules.m2_calendar import calendar_rules, tz

CURRENCY_TOKENS = ("元/间夜", "间夜", "房晚", "rmb", "cny", "元", "¥", "￥", "/")
NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


@dataclass
class CoercedRow:
    hotel_code: str
    hotel_name: str
    channel: str
    biz_date: dt.date
    stratum: str
    rooms_available: int
    rooms_sold: int
    adr: Decimal
    revenue_net: Decimal
    snapshot_utc: dt.datetime
    snapshot_local: dt.date
    late_days: int
    line_no: int
    late_flag: bool = False
    out_of_window: bool = False

    def key(self) -> tuple:
        return (self.hotel_code, self.channel, self.biz_date)


def normalize_number_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").strip()
    value = value.replace(",", "").replace(" ", "").replace("\u3000", "")
    lowered = value.lower()
    for token in CURRENCY_TOKENS:
        if lowered.endswith(token):
            value = value[: len(value) - len(token)]
            lowered = value.lower()
        if lowered.startswith(token):
            value = value[len(token):]
            lowered = value.lower()
    return value


def to_decimal(text: str) -> tuple[str, Decimal | None]:
    """返回 (状态, 值)：ok / sentinel / bad。"""
    raw = (text or "").strip()
    if raw.lower() in config.SENTINELS:
        return "sentinel", None
    normalized = normalize_number_text(raw)
    if not normalized or not NUMBER_RE.match(normalized):
        return "bad", None
    try:
        return "ok", Decimal(normalized)
    except InvalidOperation:
        return "bad", None


def to_int(text: str) -> tuple[str, int | None]:
    status, value = to_decimal(text)
    if status != "ok":
        return status, None
    if value != value.to_integral_value():
        return "bad", None
    return "ok", int(value)


def parse_biz_date(text: str) -> dt.date:
    value = (text or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError("业务日期无法解析：%s" % value)


def _reject(line_no: int, code: str, field: str, raw: str, detail: str,
            key: dict | None = None) -> dict:
    entry = {"line_no": line_no, "code": code, "field": field, "raw": raw,
             "label": reasons.label(code), "detail": detail}
    if key:
        entry.update(key)
    return entry


def coerce_row(raw: dict, settings: config.Settings) -> tuple[CoercedRow | None, list[dict]]:
    line_no = int(raw.get("_line_no", 0))
    rejects: list[dict] = []
    hotel_code = (raw.get("hotel_code") or "").strip()
    channel = (raw.get("channel") or "").strip().lower()
    hotel_name = (raw.get("hotel_name") or "").strip()
    if not hotel_code or not channel:
        return None, [_reject(line_no, "Q_MISSING_KEY", "hotel_code/channel",
                              f"{hotel_code}|{channel}", "门店编码或渠道为空")]
    try:
        biz_date = parse_biz_date(raw.get("biz_date", ""))
    except ValueError as exc:
        return None, [_reject(line_no, "Q_BAD_DATE", "biz_date", raw.get("biz_date", ""),
                              str(exc))]
    if biz_date > settings.eval_date:
        return None, [_reject(line_no, "Q_FUTURE_DATE", "biz_date", biz_date.isoformat(),
                              "业务日晚于评估日 " + settings.eval_date.isoformat())]
    key_ctx = {"hotel_code": hotel_code, "channel": channel, "biz_date": biz_date.isoformat()}
    try:
        snapshot_utc = tz.parse_instant(raw.get("updated_at", ""))
    except ValueError as exc:
        return None, [_reject(line_no, "Q_BAD_SNAPSHOT", "updated_at",
                              raw.get("updated_at", ""), str(exc), key_ctx)]
    snapshot_local = tz.local_date(snapshot_utc)
    lateness = calendar_rules.lateness_days(biz_date, snapshot_local)
    if lateness < 0:
        return None, [_reject(line_no, "Q_BAD_SNAPSHOT", "updated_at",
                              raw.get("updated_at", ""),
                              f"快照日 {snapshot_local} 早于业务日 {biz_date}")]

    values: dict[str, object] = {}
    for field in ("rooms_available", "rooms_sold"):
        status, number = to_int(raw.get(field, ""))
        if status == "sentinel":
            rejects.append(_reject(line_no, "Q_SENTINEL", field, raw.get(field, ""),
                                   "该点指标未知，不按 0 参与合计", key_ctx))
        elif status == "bad":
            rejects.append(_reject(line_no, "Q_BAD_NUMBER", field, raw.get(field, ""),
                                   "不是整数房晚", key_ctx))
        else:
            values[field] = number
    for field in ("adr", "revenue_net"):
        status, number = to_decimal(raw.get(field, ""))
        if status == "sentinel":
            rejects.append(_reject(line_no, "Q_SENTINEL", field, raw.get(field, ""),
                                   "该点指标未知，不按 0 参与合计", key_ctx))
        elif status == "bad":
            rejects.append(_reject(line_no, "Q_BAD_NUMBER", field, raw.get(field, ""),
                                   "去除千分位与单位后仍不是数字", key_ctx))
        else:
            values[field] = number.quantize(settings.money_quant)
    if rejects:
        return None, rejects
    return CoercedRow(
        hotel_code=hotel_code,
        hotel_name=hotel_name or hotel_code,
        channel=channel,
        biz_date=biz_date,
        stratum=calendar_rules.day_stratum(biz_date),
        rooms_available=int(values["rooms_available"]),
        rooms_sold=int(values["rooms_sold"]),
        adr=values["adr"],
        revenue_net=values["revenue_net"],
        snapshot_utc=snapshot_utc,
        snapshot_local=snapshot_local,
        late_days=lateness,
        line_no=line_no,
    ), []


def coerce_rows(rows: list[dict], settings: config.Settings) -> tuple[list[CoercedRow], list[dict]]:
    coerced: list[CoercedRow] = []
    rejects: list[dict] = []
    for raw in rows:
        row, problems = coerce_row(raw, settings)
        rejects.extend(problems)
        if row is not None:
            coerced.append(row)
    return coerced, rejects
