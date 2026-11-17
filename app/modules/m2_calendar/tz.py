"""时间归一：带偏移的统一到 UTC，再派生东八区业务日。

同一条数据可能写成 Z、+08:00、+0800，甚至没有偏移；没有偏移按东八区解释。
"""
from __future__ import annotations

import datetime as dt
import re

from app.config import BIZ_TZ_OFFSET

INSTANT_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}(?::\d{2})?)(?P<frac>\.\d+)?"
    r"\s*(?P<offset>Z|z|[+-]\d{2}:?\d{2})?$"
)


def parse_instant(text: str, default_tz: dt.timezone = BIZ_TZ_OFFSET) -> dt.datetime:
    value = (text or "").strip()
    if not value:
        raise ValueError("空时间")
    match = INSTANT_RE.match(value)
    if not match:
        raise ValueError("无法解析：%s" % value)
    clock = match.group("time")
    fmt = "%H:%M:%S" if clock.count(":") == 2 else "%H:%M"
    moment = dt.datetime.strptime(
        match.group("date") + " " + clock + (match.group("frac") or ""),
        "%Y-%m-%d " + fmt + (".%f" if match.group("frac") else ""))
    offset = match.group("offset")
    if offset in ("Z", "z"):
        return moment.replace(tzinfo=dt.timezone.utc)
    if offset:
        sign = 1 if offset[0] == "+" else -1
        digits = offset[1:].replace(":", "")
        delta = dt.timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        return (moment - sign * delta).replace(tzinfo=dt.timezone.utc)
    return moment.replace(tzinfo=default_tz).astimezone(dt.timezone.utc)


def local_date(instant: dt.datetime, tz: dt.timezone = BIZ_TZ_OFFSET) -> dt.date:
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=dt.timezone.utc)
    return instant.astimezone(tz).date()


def local_stamp(instant: dt.datetime, tz: dt.timezone = BIZ_TZ_OFFSET) -> str:
    return instant.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def utc_text(instant: dt.datetime) -> str:
    return instant.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
