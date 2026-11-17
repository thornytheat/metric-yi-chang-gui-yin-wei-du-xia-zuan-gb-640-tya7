"""LMDI 量价分解：净收入差 = 可售房晚差 + 出租率差 + 均价差 + 残差。

权重用对数平均 L(a, b) = (a − b) / (ln a − ln b)，a = b 时取该值本身。
三个因子的乘积严格等于净收入，残差只来自 Decimal 量化，必须显式给出。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext

PRECISION = 40
FACTORS = (
    ("rooms", "可售房晚"),
    ("occ", "出租率"),
    ("adr", "均价"),
)


@dataclass
class Decomposition:
    rev_base: Decimal
    rev_cur: Decimal
    delta: Decimal
    factors: dict = field(default_factory=dict)
    residual: Decimal = Decimal("0")
    ok: bool = True
    code: str = ""
    detail: str = ""

    def factor_sum(self) -> Decimal:
        return sum(self.factors.values(), Decimal("0"))

    def as_dict(self, quant: Decimal = Decimal("0.01")) -> dict:
        return {
            "rev_base": str(self.rev_base.quantize(quant)),
            "rev_cur": str(self.rev_cur.quantize(quant)),
            "delta": str(self.delta.quantize(quant)),
            "factors": [{"key": key, "label": label,
                         "value": str(self.factors.get(key, Decimal("0")).quantize(quant))}
                        for key, label in FACTORS],
            "residual": str(self.residual.quantize(quant)),
            "ok": self.ok,
            "code": self.code,
            "detail": self.detail,
        }


def log_mean(a: Decimal, b: Decimal) -> Decimal:
    if a == b:
        return a
    with localcontext() as ctx:
        ctx.prec = PRECISION
        try:
            return (a - b) / (a.ln() - b.ln())
        except (InvalidOperation, ZeroDivisionError):
            return Decimal("0")


def _valid(value) -> bool:
    try:
        return Decimal(value) > 0
    except (InvalidOperation, TypeError):
        return False


def decompose(rev_base, rev_cur, base_factors: dict, cur_factors: dict,
              quant: Decimal = Decimal("0.01")) -> Decomposition:
    rev_base = Decimal(rev_base)
    rev_cur = Decimal(rev_cur)
    delta = rev_cur - rev_base
    result = Decomposition(rev_base=rev_base, rev_cur=rev_cur, delta=delta)
    zero_keys = [key for key, _ in FACTORS
                 if not _valid(base_factors.get(key, 0)) or not _valid(cur_factors.get(key, 0))]
    if zero_keys:
        result.ok = False
        result.code = "A_ZERO_BASE"
        result.detail = "基期或当期因子为 0，不做对数分解"
        result.residual = delta.quantize(quant)
        return result
    with localcontext() as ctx:
        ctx.prec = PRECISION
        weight = log_mean(rev_cur, rev_base)
        values = {}
        for key, _ in FACTORS:
            ratio = Decimal(cur_factors[key]) / Decimal(base_factors[key])
            values[key] = weight * ratio.ln()
    result.factors = {key: value for key, value in values.items()}
    residual = delta - result.factor_sum()
    result.residual = residual.quantize(quant)
    return result


def identity_holds(parts: list[Decomposition], quant: Decimal = Decimal("0.01")) -> bool:
    """分解守恒：各因子 + 残差 = 总差，量化到分后逐组核对。"""
    for part in parts:
        total = part.factor_sum().quantize(quant) + part.residual
        if total != part.delta.quantize(quant):
            return False
    return True
