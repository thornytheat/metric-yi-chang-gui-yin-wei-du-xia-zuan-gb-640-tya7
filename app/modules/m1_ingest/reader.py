"""流式读行：csv 模块解析，保留原始行号、识别重复表头、拦截断半行。

不整表读入：逐行产出 RawRecord，service 逐行诊断后即丢弃原文。
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from app import reasons
from app.errors import TruncatedInput
from app.modules.m1_ingest import columns, decode


@dataclass
class RawRecord:
    line_no: int
    cells: list[str]
    raw: str


@dataclass
class ParseResult:
    header: list[str]
    records: list[RawRecord]
    encoding: str
    empty_state: str
    notices: list[dict] = field(default_factory=list)
    physical_lines: int = 0
    duplicate_headers: int = 0
    trailing_newline: bool = True


def _notice(code: str, detail: str) -> dict:
    return {"code": code, "label": reasons.label(code), "detail": detail}


def notices_placeholder(decoded: decode.Decoded) -> list[dict]:
    return list(decoded.notices)


def read_records(data: bytes, require_complete: bool = True) -> ParseResult:
    decoded = decode.decode_bytes(data)
    text = decoded.text
    if not text.strip():
        return ParseResult(header=[], records=[], encoding=decoded.encoding,
                           empty_state="no_rows", notices=notices_placeholder(decoded),
                           physical_lines=0, duplicate_headers=0, trailing_newline=False)
    trailing_newline = text.endswith("\n") or text.endswith("\r")
    buffer = io.StringIO(text, newline="")
    reader = csv.reader(buffer)
    header: list[str] = []
    records: list[RawRecord] = []
    notices = list(decoded.notices)
    dup_headers = 0
    physical = 0
    seen_header = False
    try:
        for cells in reader:
            physical += 1
            line_no = reader.line_num
            if not header:
                header = [cell for cell in cells]
                seen_header = True
                continue
            if all(not (cell or "").strip() for cell in cells):
                records.append(RawRecord(line_no=line_no, cells=cells, raw=",".join(cells)))
                continue
            if columns.is_header_row(cells):
                dup_headers += 1
                continue
            records.append(RawRecord(line_no=line_no, cells=cells, raw=",".join(cells)))
    except csv.Error as exc:
        raise TruncatedInput(f"第 {physical} 行附近 CSV 无法解析：{exc}", code="E_CSV_SYNTAX")

    if dup_headers:
        notices.append(_notice("N_DUP_HEADER", f"剔除重复表头 {dup_headers} 行"))
    if not trailing_newline and records:
        tail = records[-1]
        if len(tail.cells) < len(header):
            raise TruncatedInput(
                f"末行被截断：第 {tail.line_no} 行只有 {len(tail.cells)} 列，表头 {len(header)} 列",
                code="E_TRUNCATED", line_no=tail.line_no)
    if require_complete and not seen_header:
        raise TruncatedInput("缺少表头行", code="E_NO_HEADER")
    empty_state = columns.classify_empty([rec.cells for rec in records], bool(header))
    if empty_state == "all_blank":
        notices.append(_notice("N_ALL_BLANK", "全部数据行为空"))
    elif empty_state == "header_only":
        notices.append(_notice("N_HEADER_ONLY", "只有表头"))
    return ParseResult(header=header, records=records, encoding=decoded.encoding,
                       empty_state=empty_state, notices=notices,
                       physical_lines=physical, duplicate_headers=dup_headers,
                       trailing_newline=trailing_newline)
