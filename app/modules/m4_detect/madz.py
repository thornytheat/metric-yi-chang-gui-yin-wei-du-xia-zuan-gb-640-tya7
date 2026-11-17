"""稳健统计：中位数、MAD、平均绝对偏差、稳健 z。

MAD 为 0 时不能直接除：窗口全平的量测改用平均绝对偏差，
两者都为 0 时 z 无定义，宁可返回 None 也不制造 0 除。
"""
from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Sequence

SCALE = Decimal("0.6745")
FALLBACK = Decimal("0.7979")


def median(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(Decimal(value) for value in values)
    size = len(ordered)
    if size == 0:
        raise ValueError("空序列没有中位数")
    middle = size // 2
    if size % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def mad(values: Sequence[Decimal], center: Decimal | None = None) -> Decimal:
    mid = median(values) if center is None else Decimal(center)
    return median([abs(Decimal(value) - mid) for value in values])


def mean_abs_dev(values: Sequence[Decimal], center: Decimal | None = None) -> Decimal:
    mid = median(values) if center is None else Decimal(center)
    total = sum((abs(Decimal(value) - mid) for value in values), Decimal("0"))
    return total / Decimal(len(values))


def robust_z(value: Decimal, center: Decimal, dispersion: Decimal) -> Decimal | None:
    if dispersion == 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 28
        return (SCALE * (Decimal(value) - Decimal(center)) / Decimal(dispersion)).quantize(
            Decimal("0.0001"))


def scale_of(values: Sequence[Decimal], center: Decimal) -> tuple[Decimal, str]:
    """返回 (离散度, 计算方式)：mad / meanad / zero。"""
    spread = mad(values, center)
    if spread > 0:
        return spread, "mad"
    fallback = mean_abs_dev(values, center)
    if fallback > 0:
        return (FALLBACK * fallback).quantize(Decimal("0.0001")), "meanad"
    return Decimal("0"), "zero"
