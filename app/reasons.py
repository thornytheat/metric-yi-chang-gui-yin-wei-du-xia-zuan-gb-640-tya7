"""原因码唯一来源：隔离、缺失、重复、迟到、提示五类。

每行隔离都带一个稳定码，页面、CSV 导出、对账都引用这里的文案，避免各处自造。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reason:
    code: str
    label: str
    account: str          # quarantine / duplicate / missing / late / notice
    detail: str


REASONS: dict[str, Reason] = {r.code: r for r in (
    Reason("Q_COLUMN_COUNT", "列数不齐", "quarantine", "该行字段数与表头不一致"),
    Reason("Q_BAD_DATE", "业务日期无法解析", "quarantine", "biz_date 不是 YYYY-MM-DD"),
    Reason("Q_BAD_SNAPSHOT", "快照时间异常", "quarantine", "updated_at 无法解析或早于业务日"),
    Reason("Q_BAD_NUMBER", "数值无法归一", "quarantine", "含非数字字符，去千分位后仍不可解析"),
    Reason("Q_SENTINEL", "空值哨兵", "quarantine", "该点指标未知，不按 0 参与合计"),
    Reason("Q_NEGATIVE", "负值", "quarantine", "房晚或收入为负"),
    Reason("Q_OVERSELL", "超售越界", "quarantine", "已售超过可售的 110%"),
    Reason("Q_FUTURE_DATE", "业务日晚于评估日", "quarantine", "不允许提前上报"),
    Reason("Q_DUP_CONFLICT", "重复快照冲突", "quarantine", "同键同一快照时间给出不同数值"),
    Reason("Q_MISSING_KEY", "业务键缺失", "quarantine", "门店或渠道为空"),
    Reason("D_SUPERSEDED", "被后快照覆盖", "duplicate", "同键存在更晚快照，本行不采信"),
    Reason("M_MISSING_BUCKET", "缺桶", "missing", "网格内该门店×渠道×日期没有有效点"),
    Reason("L_LATE_ARRIVAL", "迟到补报", "late", "快照日晚于业务日超过 1 天，仍计入"),
    Reason("L_OUT_OF_WINDOW", "超迟到窗口", "late", "业务日落后水位 3 天以上，仍计入并标记"),
    Reason("N_CHARSET_FALLBACK", "编码回退", "notice", "按 UTF-8 解码失败，回退 GB18030"),
    Reason("N_CHARSET_MIXED", "同批编码不一致", "notice", "同一批内出现两种编码"),
    Reason("N_BOM_STRIPPED", "剥离 BOM", "notice", "表头首列带 BOM，已剥离"),
    Reason("N_DUP_HEADER", "重复表头", "notice", "分段导出重复表头，已剔除"),
    Reason("N_ALL_BLANK", "全空行", "notice", "所有数据行为空，按空态处理"),
    Reason("N_HEADER_ONLY", "仅表头", "notice", "只有表头没有数据行"),
    Reason("N_NO_MAD", "离散度为 0", "notice", "同层窗口全部相同，改用平均绝对偏差"),
    Reason("N_Z_UNDEFINED", "z 不可计算", "notice", "平均绝对偏差也为 0，本点不判"),
    Reason("R_NO_DISPERSION", "基线离散度为 0", "review", "剔过节假日后基线仍全等，本卡不判"),
    Reason("R_HOLIDAY_THIN", "节假日分层不足", "review", "节假日占基线三成以上，剔除后结论可能不成立"),
    Reason("R_CORRELATED", "同批异常", "review", "同日同向且门店互不相同的确认点归为一组"),
    Reason("R_FLIP", "敏感性翻转", "review", "换窗口或阈值后结论不再成立"),
    Reason("R_ZERO_BASE", "因子无法分解", "review", "基期或当期因子为 0，差额全进残差"),
)}

ACCOUNTS = ("quarantine", "duplicate", "missing", "late", "notice", "review")

# 复盘卡的稳定码：编排层与本模块共用同一份字面量
REVIEW_NO_DISPERSION = "R_NO_DISPERSION"
REVIEW_HOLIDAY_THIN = "R_HOLIDAY_THIN"
REVIEW_CORRELATED = "R_CORRELATED"
REVIEW_FLIP = "R_FLIP"
REVIEW_ZERO_BASE = "R_ZERO_BASE"

EMPTY_STATES = {
    "no_rows": "E_NO_ROWS",
    "header_only": "E_HEADER_ONLY",
    "all_blank": "E_ALL_BLANK",
}


def label(code: str) -> str:
    reason = REASONS.get(code)
    return reason.label if reason else code


def detail(code: str) -> str:
    reason = REASONS.get(code)
    return reason.detail if reason else ""


def account(code: str) -> str:
    reason = REASONS.get(code)
    return reason.account if reason else "notice"


def codes_of(kind: str) -> list[str]:
    return [code for code, reason in REASONS.items() if reason.account == kind]


def counts_by_account(entries: list[dict]) -> dict[str, int]:
    out = {kind: 0 for kind in ACCOUNTS}
    for entry in entries:
        out[account(entry.get("code", ""))] = out.get(account(entry.get("code", "")), 0) + 1
    return out
