"""敏感性扫描：窗口与阈值各取三档，看每条结论是否还成立。

九个组合（3 窗口 × 3 阈值）各跑一次检测；一条事件在某个组合下「仍成立」=
该组合里同一序列在事件区间内、方向一致的确认天数达到事件天数的一半。
任何一个组合不成立就记为翻转，结论必须标 unstable；只报基准组合的结论
等于把不稳定的判断当成稳定的用。

本文件只做组合、比对与结论标注，检测由编排层调用 M4 完成。
"""
from __future__ import annotations

AXIS_WINDOWS = (21, 28, 35)
AXIS_THRESHOLDS = ("2.5", "3.5", "4.5")
KEEP_NUM = 1
KEEP_DEN = 2
STABLE = "stable"
UNSTABLE = "unstable"


def combos() -> list[dict]:
    return [{"key": f"{window}x{threshold}", "window": window, "threshold": threshold}
            for window in AXIS_WINDOWS for threshold in AXIS_THRESHOLDS]


def base_key(settings) -> str:
    return f"{settings.window_points}x{settings.z_threshold}"


def axis_label(settings) -> str:
    return f"{settings.window_points} 点 × |z| {settings.z_threshold}"


def event_id(event: dict) -> str:
    return "EV-%s-%s" % (event["series"].replace("/", "-"), event["start"].replace("-", ""))


def kept_days(event: dict, detections: list[dict]) -> int:
    """事件区间内、同序列同方向的确认天数。"""
    series_name = event["series"]
    start, end = event["start"], event["end"]
    return sum(1 for item in detections
               if item.get("series") == series_name
               and item.get("status") == "confirmed"
               and item.get("direction") == event["direction"]
               and start <= item.get("biz_date", "") <= end)


def holds(event: dict, detections: list[dict]) -> bool:
    days = max(1, int(event["days"]))
    return kept_days(event, detections) * KEEP_DEN >= days * KEEP_NUM


def coverage(event: dict, detections: list[dict]) -> dict:
    kept = kept_days(event, detections)
    return {"days": int(event["days"]), "kept_days": kept,
            "holds": kept * KEEP_DEN >= max(1, int(event["days"])) * KEEP_NUM}


def evaluate(events: list[dict], by_combo: dict[str, list[dict]],
             combo_list: list[dict] | None = None) -> dict:
    """逐事件给翻转清单与结论，逐组合给保留/翻转计数。"""
    active_combos = combo_list if combo_list is not None else combos()
    counts = {combo["key"]: {"kept": 0, "flipped": 0} for combo in active_combos}
    rows: list[dict] = []
    for event in events:
        flipped: list[str] = []
        coverage_rows = []
        for combo in active_combos:
            detections = by_combo.get(combo["key"], [])
            detail = coverage(event, detections)
            coverage_rows.append({"key": combo["key"], **detail})
            if detail["holds"]:
                counts[combo["key"]]["kept"] += 1
            else:
                counts[combo["key"]]["flipped"] += 1
                flipped.append(combo["key"])
        rows.append({
            "event_id": event_id(event),
            "series": event["series"],
            "start": event["start"],
            "end": event["end"],
            "days": int(event["days"]),
            "direction": event["direction"],
            "hold": len(active_combos) - len(flipped),
            "flip": len(flipped),
            "flipped": flipped,
            "coverage": coverage_rows,
            "verdict": STABLE if not flipped else UNSTABLE,
        })
    combo_rows = [{**combo, **counts[combo["key"]]} for combo in active_combos]
    stable = sum(1 for row in rows if row["verdict"] == STABLE)
    return {
        "events": rows,
        "combos": combo_rows,
        "stable": stable,
        "unstable": len(rows) - stable,
        "verdict": STABLE if not rows or stable == len(rows) else UNSTABLE,
        "hold_sum": sum(row["hold"] for row in rows),
        "flip_sum": sum(row["flip"] for row in rows),
    }


def verdict_of(evaluation: dict, eid: str) -> str:
    for row in evaluation.get("events", []):
        if row["event_id"] == eid:
            return row["verdict"]
    return STABLE


def flips_of(evaluation: dict, eid: str) -> list[str]:
    for row in evaluation.get("events", []):
        if row["event_id"] == eid:
            return list(row["flipped"])
    return []
