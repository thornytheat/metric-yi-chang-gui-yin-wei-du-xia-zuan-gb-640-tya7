"""表头归一与列映射：别名、列数、空态分类。

历史导出用过中文列名与缩写，别名表集中在 app/config.py；这里只做映射与判定。
"""
from __future__ import annotations

from app import config

REQUIRED = config.REQUIRED_COLUMNS


def normalize_header(cell: str) -> str:
    name = (cell or "").replace("\ufeff", "").strip().strip('"').strip("'")
    name = name.replace(" ", "").replace("\u3000", "")
    lowered = name.lower()
    return config.COLUMN_ALIASES.get(name, config.COLUMN_ALIASES.get(lowered, lowered))


def map_columns(header: list[str]) -> tuple[dict[str, int], list[str], list[str], list[str]]:
    """返回 (canonical→index, 缺失列, 未知列, 重复列)。"""
    mapping: dict[str, int] = {}
    duplicated: list[str] = []
    unknown: list[str] = []
    for index, raw in enumerate(header):
        name = normalize_header(raw)
        if not name:
            continue
        if name in mapping:
            duplicated.append(name)
            continue
        if name in REQUIRED:
            mapping[name] = index
        else:
            unknown.append(raw.strip())
    missing = [name for name in REQUIRED if name not in mapping]
    return mapping, missing, unknown, duplicated


def is_header_row(cells: list[str]) -> bool:
    hits = 0
    for cell in cells:
        if normalize_header(cell) in REQUIRED:
            hits += 1
    return hits >= max(3, len(REQUIRED) // 2)


def row_cells(cells: list[str], mapping: dict[str, int]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, index in mapping.items():
        out[name] = cells[index].strip() if index < len(cells) else ""
    return out


def classify_empty(data_rows: list[list[str]], has_header: bool) -> str:
    """0 行 / 全空行 / 仅表头 三态分开，空串表示有数据可采信。"""
    if not has_header and not data_rows:
        return "no_rows"
    if not data_rows:
        return "header_only"
    non_blank = [cells for cells in data_rows if any((cell or "").strip() for cell in cells)]
    if not non_blank:
        return "all_blank"
    return ""
