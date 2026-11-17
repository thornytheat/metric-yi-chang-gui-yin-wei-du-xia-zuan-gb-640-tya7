"""复盘编排：确认异常 → 复盘卡 → 敏感性扫描 → 同批异常。

一张复盘卡回答四件事：基线是什么、掉在哪一因子、下一步查什么、这张卡稳不稳。
基线由 M7 自己按剔除节假日的窗口重算（不复用 M4 的判定），
敏感性扫描用九个组合重跑 M4，翻转必须写在卡上，不能只报基准结论。
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal, InvalidOperation

from app import config, reasons
from app.modules.m4_detect import service as detect_service
from app.modules.m5_attrib import lmdi
from app.modules.m5_attrib import service as batch_service
from app.modules.m7_review import baseline, corr, sensitiv

ACTIONS = {
    "rooms": ("A_ROOMS", "核对可售房晚"),
    "occ": ("A_OCC", "核对已售与超售"),
    "adr": ("A_ADR", "核对均价与折让"),
}
ACTION_BASE = ("A_BASE", "补齐基期房量")
ACTION_RECHECK = ("A_RECHECK", "换窗口复核")
CONFIDENCE_ORDER = ("low", "medium", "high")
WINDOW_MIN, WINDOW_MAX = 14, 42
THRESHOLD_MIN, THRESHOLD_MAX = Decimal("1"), Decimal("10")


def _series_rows(points: list[dict], hotel_code: str, channel: str) -> list[dict]:
    rows = [point for point in points
            if point["hotel_code"] == hotel_code and point["channel"] == channel]
    rows.sort(key=lambda row: row["biz_date"])
    return rows


def _window_factors(rows: list[dict], start: dt.date, end: dt.date) -> dict:
    avail = sold = Decimal("0")
    revenue = Decimal("0")
    for row in rows:
        day = dt.date.fromisoformat(row["biz_date"])
        if not (start <= day < end):
            continue
        avail += Decimal(int(row["rooms_available"]))
        sold += Decimal(int(row["rooms_sold"]))
        revenue += Decimal(row["revenue_net"])
    occ = (sold / avail) if avail > 0 else Decimal("0")
    adr = (revenue / sold) if sold > 0 else Decimal("0")
    return {"rooms": avail, "occ": occ, "adr": adr, "rev": revenue}


def _dominant(factors: dict) -> str:
    keys = ("rooms", "occ", "adr")
    return max(keys, key=lambda key: (abs(Decimal(factors[key])), -keys.index(key)))


def _factors_payload(event: dict, rows: list[dict], money_quant: Decimal) -> dict:
    start = dt.date.fromisoformat(event["start"])
    end = dt.date.fromisoformat(event["end"]) + dt.timedelta(days=1)
    span = dt.timedelta(days=max(1, int(event["days"])))
    base_factors = _window_factors(rows, start - span, start)
    cur_factors = _window_factors(rows, start, end)
    part = lmdi.decompose(base_factors["rev"], cur_factors["rev"],
                          base_factors, cur_factors, money_quant)
    payload = part.as_dict(money_quant)
    payload["dominant"] = "" if not part.ok else _dominant(part.factors)
    payload["base_rev"] = str(base_factors["rev"].quantize(money_quant))
    payload["cur_rev"] = str(cur_factors["rev"].quantize(money_quant))
    return payload


def _actions(factors: dict, flags: list[dict]) -> list[dict]:
    out: list[dict] = []
    if not factors["ok"]:
        out.append({"code": ACTION_BASE[0], "label": ACTION_BASE[1]})
    elif factors.get("dominant"):
        code, label = ACTIONS[factors["dominant"]]
        out.append({"code": code, "label": label})
    if any(flag["code"] == reasons.REVIEW_FLIP for flag in flags):
        out.append({"code": ACTION_RECHECK[0], "label": ACTION_RECHECK[1]})
    return out


def _flag(code: str, detail: str, level: str) -> dict:
    return {"code": code, "label": reasons.label(code), "detail": detail, "level": level}


def _confidence(flags: list[dict]) -> str:
    """低置信只要有一条 low；没有 low 但有 medium 记中；否则高。"""
    levels = {flag["level"] for flag in flags}
    if "low" in levels:
        return "low"
    if "medium" in levels:
        return "medium"
    return "high"


def _evidence(rows: list[dict], event: dict, detections: list[dict],
              window_points: int, threshold: Decimal) -> list[dict]:
    by_date = {item["biz_date"]: item for item in detections
               if item["series"] == event["series"]}
    out = []
    start, end = event["start"], event["end"]
    for index, row in enumerate(rows):
        if not (start <= row["biz_date"] <= end):
            continue
        base = baseline.review_baseline(rows, index, window_points)
        hit = by_date.get(row["biz_date"], {})
        out.append({
            "biz_date": row["biz_date"],
            "value": str(Decimal(row["revenue_net"]).quantize(Decimal("0.01"))),
            "median": str(base["median"].quantize(Decimal("0.01"))),
            "dispersion": str(base["dispersion"].quantize(Decimal("0.0001"))),
            "method": base["method"],
            "z": hit.get("z"),
            "status": hit.get("status", ""),
            "direction": hit.get("direction", ""),
        })
    return out


def card(event: dict, points: list[dict], detections: list[dict], settings: config.Settings,
         evaluation: dict, groups: list[dict]) -> dict:
    """一张复盘卡：基线、因子、动作、置信、证据行。"""
    rows = _series_rows(points, event["hotel_code"], event["channel"])
    index = next((position for position, row in enumerate(rows)
                  if row["biz_date"] == event["start"]), None)
    if index is None:
        return {}
    value = Decimal(rows[index]["revenue_net"])
    base = baseline.review_baseline(rows, index, settings.window_points)
    eid = sensitiv.event_id(event)
    verdict = sensitiv.verdict_of(evaluation, eid)
    flags: list[dict] = []

    review_code = baseline.review_z(value, base)[1]
    if review_code:
        flags.append(_flag(review_code, "剔过节假日后基线仍全等，本卡不判", "low"))
    holiday = baseline.holiday_flag(base, value, settings.z_threshold)
    if holiday:
        level = "low" if holiday["downgrade"] else "medium"
        flags.append(_flag(holiday["code"], holiday["detail"], level))
    group = corr.group_of(event, groups)
    if group:
        flags.append(_flag(reasons.REVIEW_CORRELATED,
                           "同批 %d 家门店同向" % group["hotel_count"], "medium"))
    factors = _factors_payload(event, rows, settings.money_quant)
    if not factors["ok"]:
        flags.append(_flag(reasons.REVIEW_ZERO_BASE,
                           "基期或当期因子为 0，差额全进残差", "medium"))
    flips = sensitiv.flips_of(evaluation, eid)
    if verdict == sensitiv.UNSTABLE:
        flags.append(_flag(reasons.REVIEW_FLIP,
                           "敏感性翻转 %d 个组合" % len(flips), "low"))

    confidence = _confidence(flags)
    baseline_payload = baseline.as_payload(base, value, settings.z_threshold)
    baseline_payload["window"] = settings.window_points
    return {
        "event_id": eid,
        "series": event["series"],
        "hotel_code": event["hotel_code"],
        "channel": event["channel"],
        "start": event["start"],
        "end": event["end"],
        "days": int(event["days"]),
        "direction": event["direction"],
        "stratum": event["stratum"],
        "delta": event["delta"],
        "peak_z": event["peak_z"],
        "baseline": baseline_payload,
        "factors": factors,
        "actions": _actions(factors, flags),
        "flags": flags,
        "confidence": confidence,
        "verdict": verdict,
        "flipped": flips,
        "group_id": group["group_id"] if group else "",
        "peers": corr.peers(event, detections),
        "evidence": _evidence(rows, event, detections, settings.window_points,
                              settings.z_threshold),
    }


def sensitivity(settings: config.Settings, points: list[dict],
                events: list[dict]) -> dict:
    """九个组合各跑一次检测，逐事件给翻转清单。"""
    combo_list = sensitiv.combos()
    by_combo: dict[str, list[dict]] = {}
    summary: dict[str, dict] = {}
    for combo in combo_list:
        active = dataclasses.replace(settings,
                                     window_points=combo["window"],
                                     z_threshold=Decimal(combo["threshold"]))
        result = detect_service.detect(points, active)
        by_combo[combo["key"]] = result["detections"]
        summary[combo["key"]] = result["summary"]
    evaluation = sensitiv.evaluate(events, by_combo, combo_list)
    base = sensitiv.base_key(settings)
    for row in evaluation["combos"]:
        row["confirmed_points"] = summary[row["key"]]["confirmed_points"]
        row["events"] = summary[row["key"]]["events"]
        row["is_base"] = row["key"] == base
    evaluation["axis"] = {"windows": list(sensitiv.AXIS_WINDOWS),
                          "thresholds": list(sensitiv.AXIS_THRESHOLDS),
                          "base": base,
                          "label": sensitiv.axis_label(settings)}
    return evaluation


def review(settings: config.Settings, store, run_id: str | None = None) -> dict:
    run = store.get_run(run_id) if run_id else store.latest_run()
    if run is None:
        return {}
    points = batch_service.load_view(store, run["run_id"])
    if not points:
        return {"run_id": run["run_id"], "empty": True, "cards": [], "sensitivity": {},
                "groups": [], "summary": {}, "marks": [], "run": run}
    detection = detect_service.detect(points, settings)
    events = detection["events"]
    evaluation = sensitivity(settings, points, events)
    groups = corr.clusters(detection["detections"])
    cards = [card(event, points, detection["detections"], settings, evaluation, groups)
             for event in events]
    cards = [item for item in cards if item]
    cards.sort(key=lambda item: (item["confidence"] != "low",
                                 item["verdict"] != sensitiv.UNSTABLE,
                                 -abs(Decimal(item["delta"])), item["event_id"]))
    summary = {
        "cards": len(cards),
        "high": sum(1 for item in cards if item["confidence"] == "high"),
        "medium": sum(1 for item in cards if item["confidence"] == "medium"),
        "low": sum(1 for item in cards if item["confidence"] == "low"),
        "stable": evaluation["stable"],
        "unstable": evaluation["unstable"],
        "groups": len(groups),
        "group_hotels": corr.count_hotels(groups),
        "degenerate": sum(1 for item in cards
                          if item["baseline"]["method"] == "zero"),
        "holiday_cards": sum(1 for item in cards
                             if any(flag["code"] == reasons.REVIEW_HOLIDAY_THIN
                                    for flag in item["flags"])),
        "events": len(events),
        "axis": evaluation["axis"]["label"],
    }
    marks = _marks(detection["detections"])
    return {
        "run_id": run["run_id"],
        "empty": False,
        "run": run,
        "cards": cards,
        "sensitivity": evaluation,
        "groups": groups,
        "summary": summary,
        "marks": marks,
        "cards_csv_href": "/api/review/cards.csv?run_id=" + run["run_id"],
    }


def _marks(detections: list[dict]) -> list[dict]:
    buckets: dict[str, dict] = {}
    for item in detections:
        if item["status"] not in ("confirmed", "candidate"):
            continue
        bucket = buckets.setdefault(item["biz_date"],
                                    {"biz_date": item["biz_date"],
                                     "status": "candidate", "count": 0})
        bucket["count"] += 1
        if item["status"] == "confirmed":
            bucket["status"] = "confirmed"
    return [buckets[day] for day in sorted(buckets)]


def sensitivity_point(settings: config.Settings, store, run_id: str | None,
                      window: int, threshold: Decimal) -> dict:
    """单组合明细：这个窗口与阈值下还剩下哪些事件。"""
    active = dataclasses.replace(settings, window_points=window, z_threshold=threshold)
    run = store.get_run(run_id) if run_id else store.latest_run()
    if run is None:
        return {}
    points = batch_service.load_view(store, run["run_id"])
    if not points:
        return {"run_id": run["run_id"], "empty": True}
    base = detect_service.detect(points, settings)
    result = detect_service.detect(points, active)
    rows = []
    for event in base["events"]:
        detail = sensitiv.coverage(event, result["detections"])
        rows.append({"event_id": sensitiv.event_id(event), "series": event["series"],
                     "start": event["start"], "end": event["end"],
                     **detail,
                     "verdict": (sensitiv.STABLE if detail["holds"]
                                 else sensitiv.UNSTABLE)})
    return {
        "run_id": run["run_id"],
        "axis": {"window": window, "threshold": str(threshold),
                 "base": sensitiv.axis_label(settings)},
        "summary": result["summary"],
        "events": rows,
        "held": sum(1 for row in rows if row["holds"]),
        "flipped": sum(1 for row in rows if not row["holds"]),
    }


def parse_axis(window: str, threshold: str) -> tuple[int, Decimal]:
    """窗口与阈值先校验再算，越界给 4xx 而不是静默取默认值。"""
    from app import errors

    try:
        window_value = int(window)
    except (TypeError, ValueError) as exc:
        raise errors.BadInput("窗口不是整数：" + str(window), code="E_BAD_AXIS") from exc
    if not (WINDOW_MIN <= window_value <= WINDOW_MAX):
        raise errors.BadInput("窗口只支持 %d-%d 天" % (WINDOW_MIN, WINDOW_MAX),
                              code="E_BAD_AXIS")
    try:
        threshold_value = Decimal(str(threshold))
    except (InvalidOperation, TypeError) as exc:
        raise errors.BadInput("阈值不是数字：" + str(threshold), code="E_BAD_AXIS") from exc
    if not (THRESHOLD_MIN <= threshold_value <= THRESHOLD_MAX):
        raise errors.BadInput("阈值只支持 1-10", code="E_BAD_AXIS")
    return window_value, threshold_value
