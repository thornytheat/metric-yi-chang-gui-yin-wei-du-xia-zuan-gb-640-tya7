"""字段定义与规则说明：列名、类型、单位、精度、可空，供 /api/contract 与字典页引用。"""
from __future__ import annotations

FIELDS = (
    {"name": "hotel_code", "cn": "门店编码", "type": "string", "unit": "", "precision": 0,
     "nullable": False, "note": "前导零保留，不转数字"},
    {"name": "hotel_name", "cn": "门店名", "type": "string", "unit": "", "precision": 0,
     "nullable": False, "note": ""},
    {"name": "channel", "cn": "渠道", "type": "enum", "unit": "", "precision": 0,
     "nullable": False, "note": "ota / direct / member"},
    {"name": "biz_date", "cn": "业务日期", "type": "date", "unit": "", "precision": 0,
     "nullable": False, "note": "东八区自然日，桶为 [00:00, 24:00)"},
    {"name": "rooms_available", "cn": "可售房晚", "type": "integer", "unit": "间夜",
     "precision": 0, "nullable": False, "note": ""},
    {"name": "rooms_sold", "cn": "已售房晚", "type": "integer", "unit": "间夜",
     "precision": 0, "nullable": False, "note": ""},
    {"name": "adr", "cn": "平均房价", "type": "decimal", "unit": "元/间夜", "precision": 2,
     "nullable": False, "note": "含税报价按间夜摊，用于均价对比"},
    {"name": "revenue_net", "cn": "净收入", "type": "decimal", "unit": "元", "precision": 2,
     "nullable": False, "note": "扣除佣金与折让后的入账金额"},
    {"name": "updated_at", "cn": "快照时间", "type": "instant", "unit": "", "precision": 0,
     "nullable": False, "note": "带偏移的瞬时时间，统一折算 UTC 后比较"},
)

DERIVED = (
    {"name": "occ", "cn": "出租率", "formula": "已售房晚 / 可售房晚",
     "unit": "比值", "precision": 4},
    {"name": "adr_eff", "cn": "实现均价", "formula": "净收入 / 已售房晚",
     "unit": "元/间夜", "precision": 2},
    {"name": "rev", "cn": "期间净收入", "formula": "净收入按半开区间 [start, end) 求和",
     "unit": "元", "precision": 2},
)

RULES = (
    {"key": "stratum", "label": "周末分层",
     "definition": "工作日与休息日分开取基线；法定节假日并入休息日"},
    {"key": "window", "label": "基线窗口",
     "definition": "取当日之前最近 28 个同层点，区间左闭右开，不含当日"},
    {"key": "zscore", "label": "稳健 z",
     "definition": "z = 0.6745 × (当期值 − 中位数) / MAD；MAD 为 0 时改用平均绝对偏差"},
    {"key": "confirm", "label": "连续确认",
     "definition": "|z| 达到 3.5 记为候选，相邻业务日同向连续 2 天记为确认异常"},
    {"key": "late", "label": "迟到标记",
     "definition": "快照日晚于业务日超过 1 天为迟到，仍计入并单列账"},
    {"key": "high", "label": "偏高方向", "definition": "z 为正记偏高，为负记偏低"},
)

GRID = {
    "key": ["hotel_code", "channel", "biz_date"],
    "rule": "门店 × 渠道 × 业务日 全组合为应有网格点",
    "range": "在本批次有效业务日的 [min, max+1) 区间内枚举",
}


def payload() -> dict:
    return {"fields": [dict(item) for item in FIELDS],
            "derived": [dict(item) for item in DERIVED],
            "rules": [dict(item) for item in RULES],
            "grid": dict(GRID)}
