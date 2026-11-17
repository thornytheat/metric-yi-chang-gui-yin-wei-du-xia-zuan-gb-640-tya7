"""采信编排：解码 → 读行 → 列映射 → 逐行诊断。

只做“真实文件长什么样”，不做数值归一（那是 M2）与业务判定（那是 M3）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app import config, manifest, reasons
from app.errors import BadInput, EmptyDataset
from app.modules.m1_ingest import columns, reader


@dataclass
class IngestResult:
    input_name: str
    sha256: str
    size_bytes: int
    encoding: str
    header: list[str]
    mapping: dict[str, int]
    missing_columns: list[str]
    unknown_columns: list[str]
    duplicated_columns: list[str]
    rows: list[dict] = field(default_factory=list)
    rejects: list[dict] = field(default_factory=list)
    notices: list[dict] = field(default_factory=list)
    empty_state: str = ""
    total_records: int = 0
    physical_lines: int = 0
    duplicate_headers: int = 0
    expected_columns: int = 0


def ingest(data: bytes, input_name: str, settings: config.Settings) -> IngestResult:
    parsed = reader.read_records(data)
    if parsed.empty_state == "no_rows":
        raise EmptyDataset("没有可采信的数据行（no_rows）",
                           code=reasons.EMPTY_STATES["no_rows"], empty_state="no_rows")
    mapping, missing, unknown, duplicated = columns.map_columns(parsed.header)
    if missing:
        raise BadInput(
            "缺少必需列：" + "、".join(missing), code="E_MISSING_COLUMNS", missing=missing)
    if parsed.empty_state:
        raise EmptyDataset(
            "没有可采信的数据行（" + parsed.empty_state + "）",
            code=reasons.EMPTY_STATES[parsed.empty_state],
            empty_state=parsed.empty_state,
        )
    expected = len(parsed.header)
    rejects: list[dict] = []
    rows: list[dict] = []
    for record in parsed.records:
        if len(record.cells) != expected:
            rejects.append({
                "line_no": record.line_no,
                "code": "Q_COLUMN_COUNT",
                "field": "row",
                "raw": record.raw,
                "detail": f"期望 {expected} 列，实收 {len(record.cells)} 列",
            })
            continue
        row = columns.row_cells(record.cells, mapping)
        row["_line_no"] = record.line_no
        rows.append(row)
    result = IngestResult(
        input_name=input_name,
        sha256=manifest.sha256_bytes(data),
        size_bytes=len(data),
        encoding=parsed.encoding,
        header=parsed.header,
        mapping=mapping,
        missing_columns=missing,
        unknown_columns=unknown,
        duplicated_columns=duplicated,
        rows=rows,
        rejects=rejects,
        notices=parsed.notices,
        empty_state=parsed.empty_state,
        total_records=len(parsed.records),
        physical_lines=parsed.physical_lines,
        duplicate_headers=parsed.duplicate_headers,
        expected_columns=expected,
    )
    if not rows:
        raise EmptyDataset("没有可采信的数据行", code="E_NO_ROWS", empty_state="no_rows")
    return result
