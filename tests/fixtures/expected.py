"""样例数据的固定期望值：与 sample_data 一起改动，测试只引用这里。"""
from __future__ import annotations

from decimal import Decimal

# 主样例：4 门店 × 3 渠道 × 84 天
GRID_COMBOS = 12
GRID_DAYS = 84
GRID_POINTS = GRID_COMBOS * GRID_DAYS
MISSING_BUCKETS = 2                 # H04/member 缺 10-05 与 10-06
CONFLICT_CODE = "Q_DUP_CONFLICT"
SUPERSEDED_CODE = "D_SUPERSEDED"
WATERMARK = "2026-11-21"

# 注入的异常：两段连续两天的下跌，一天的单点冲高
CONFIRMED = (
    ("H02", "ota", "2026-11-17"),
    ("H02", "ota", "2026-11-18"),
    ("H01", "member", "2026-11-14"),
    ("H01", "member", "2026-11-15"),
)
CANDIDATE_ONLY = ("H03", "direct", "2026-11-19")

# 值归一专用样例
THOUSANDS_ADR = Decimal("1234.56")
FULLWIDTH_ADR = Decimal("1234.50")
UNIT_ADR = Decimal("980.00")

# 迟到注入：快照日落后业务日 3 天（窗内）；1 条落后 9 天（超窗）
LATE_POINTS = 10
OUT_OF_WINDOW_POINTS = 1
LATE_ALLOWED = 1
LATE_WINDOW = 3

# 阈值与窗口
Z_THRESHOLD = Decimal("3.5")
WINDOW_POINTS = 28
MIN_BASELINE = 14
