"""集中配置：窗口、阈值、分层日历、量化刻度与样例目录。

所有会改变结论的数字只在这里出现一次，测试从同一来源读取。
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from decimal import Decimal

RULES_VERSION = "anomaly-rules-2026.11.1"

# 业务时区固定为东八区：业务日 = 本地 00:00 起算的 24 小时桶
BIZ_TZ_OFFSET = dt.timezone(dt.timedelta(hours=8))

# 法定节假日：分层判定用，样例期（2026-08/11）不含节假日，单测直接喂国庆日期
HOLIDAYS = {
    dt.date(2026, 1, 1), dt.date(2026, 1, 2), dt.date(2026, 1, 3),
    dt.date(2026, 2, 15), dt.date(2026, 2, 16), dt.date(2026, 2, 17),
    dt.date(2026, 4, 4), dt.date(2026, 4, 5), dt.date(2026, 4, 6),
    dt.date(2026, 5, 1), dt.date(2026, 5, 2), dt.date(2026, 5, 3),
    dt.date(2026, 6, 19), dt.date(2026, 6, 20), dt.date(2026, 6, 21),
    dt.date(2026, 9, 25), dt.date(2026, 9, 26), dt.date(2026, 9, 27),
    dt.date(2026, 10, 1), dt.date(2026, 10, 2), dt.date(2026, 10, 3),
    dt.date(2026, 10, 4), dt.date(2026, 10, 5), dt.date(2026, 10, 6),
    dt.date(2026, 10, 7),
}

# 属性列同义词：历史导出用过中文列名与缩写
COLUMN_ALIASES = {
    "hotel_code": "hotel_code",
    "hotel": "hotel_code",
    "hotel_id": "hotel_code",
    "门店编码": "hotel_code",
    "酒店编码": "hotel_code",
    "hotel_name": "hotel_name",
    "门店": "hotel_name",
    "hotelname": "hotel_name",
    "channel": "channel",
    "渠道": "channel",
    "biz_date": "biz_date",
    "业务日期": "biz_date",
    "date": "biz_date",
    "rooms_available": "rooms_available",
    "可售房晚": "rooms_available",
    "avail": "rooms_available",
    "rooms_sold": "rooms_sold",
    "已售房晚": "rooms_sold",
    "sold": "rooms_sold",
    "adr": "adr",
    "平均房价": "adr",
    "revenue_net": "revenue_net",
    "净收入": "revenue_net",
    "rev": "revenue_net",
    "revenue": "revenue_net",
    "updated_at": "updated_at",
    "更新时间": "updated_at",
    "snapshot_at": "updated_at",
}

REQUIRED_COLUMNS = (
    "hotel_code", "hotel_name", "channel", "biz_date",
    "rooms_available", "rooms_sold", "adr", "revenue_net", "updated_at",
)

# 空值哨兵：与 null、空串三态分开，哨兵一律按“未知”处理，不参与合计
SENTINELS = {"-", "--", "—", "n/a", "na", "null", "none", "nan", "未知", "空", "待补", ""}

CHANNEL_LABELS = {"ota": "OTA", "direct": "直销", "member": "会员"}
STRATUM_LABELS = {"workday": "工作日", "restday": "休息日"}


@dataclass(frozen=True)
class Settings:
    db_path: str = os.environ.get("ANOMALYATTR_DB", "/data/anomalyattr.sqlite")
    sample_dir: str = os.environ.get(
        "ANOMALYATTR_SAMPLE_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_data"),
    )
    eval_date: dt.date = field(
        default_factory=lambda: dt.date.fromisoformat(os.environ.get("EVAL_DATE", "2026-11-22")))
    # 稳健 z：滚动 28 个同层历史点；同层点数不足 14 不判
    window_points: int = 28
    min_baseline_points: int = 14
    z_threshold: Decimal = Decimal("3.5")
    confirm_days: int = 2
    # 迟到：快照本地日期晚于业务日 1 天以上算迟到；业务日落后水位 3 天以上标超窗
    late_days_allowed: int = 1
    late_window_days: int = 3
    # 超售容忍：已售 > 可售 110% 视为录入错误
    oversell_limit: Decimal = Decimal("1.1")
    # 维度下钻：前 5 组单列，余下合并为「其他」
    top_n: int = 5
    dim_quant: Decimal = Decimal("0.0001")
    money_quant: Decimal = Decimal("0.01")
    intensity_quant: Decimal = Decimal("0.0001")
    rules_version: str = RULES_VERSION
    default_sample: str = "daily_metrics_dirty.csv"
    seed_on_start: bool = os.environ.get("ANOMALYATTR_SEED", "1") == "1"

    def params_digest_payload(self) -> dict:
        return {
            "window_points": self.window_points,
            "min_baseline_points": self.min_baseline_points,
            "z_threshold": str(self.z_threshold),
            "confirm_days": self.confirm_days,
            "late_days_allowed": self.late_days_allowed,
            "late_window_days": self.late_window_days,
            "oversell_limit": str(self.oversell_limit),
            "top_n": self.top_n,
            "tz": "UTC+08:00",
        }
