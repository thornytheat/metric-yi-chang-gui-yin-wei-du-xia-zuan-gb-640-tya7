"""M1 指标接入：编码回退、BOM、列数、空态三态、截断。"""
from __future__ import annotations

import pathlib

import pytest

from app import config
from app.errors import BadInput, EmptyDataset, TruncatedInput
from app.modules.m1_ingest import columns, decode, reader, service

SAMPLE_DIR = pathlib.Path(__file__).resolve().parent.parent / "sample_data"


def _settings() -> config.Settings:
    return config.Settings(db_path="/tmp/unused.sqlite", sample_dir=str(SAMPLE_DIR),
                           seed_on_start=False)


def test_bom_stripped_before_header_mapping(dirty_bytes):
    # TRAP-02：首列名带 BOM，按列名取值会全空
    decoded = decode.decode_bytes(dirty_bytes)
    assert decoded.bom_stripped is True
    result = service.ingest(dirty_bytes, "dirty.csv", _settings())
    assert result.header[0] == "hotel_code"
    assert result.mapping["hotel_code"] == 0
    assert any(item["code"] == "N_BOM_STRIPPED" for item in result.notices)


def test_gb18030_fallback_decodes_chinese_headers():
    # TRAP-01：声明 UTF-8 其实是 GB18030
    data = (SAMPLE_DIR / "daily_metrics_gb18030.csv").read_bytes()
    result = service.ingest(data, "gb.csv", _settings())
    assert result.encoding == "gb18030"
    assert result.rows[0]["hotel_name"].startswith("蓝湾")
    assert len(result.rows) == 126


def test_naive_utf8_decode_would_fail():
    # TRAP-01 反例：朴素 open(encoding="utf-8") 在这种字节上直接抛错
    data = ("hotel_code,hotel_name,channel,biz_date,rooms_available,rooms_sold,adr,"
            "revenue_net,updated_at\nH01,蓝湾·滨海,ota,2026-11-01,180,120,700.00,"
            "84000.00,2026-11-02T06:00:00+08:00\n").encode("gb18030")
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")
    decoded = decode.decode_bytes(data)
    assert decoded.encoding == "gb18030"
    assert decoded.fallback is True


def test_repeated_header_segment_dropped():
    payload = ["hotel_code,hotel_name,channel,biz_date,rooms_available,rooms_sold,adr,"
               "revenue_net,updated_at"]
    row = "H01,蓝湾·滨海,ota,2026-11-01,180,120,700.00,84000.00,2026-11-02T06:00:00+08:00"
    payload += [row, row.replace("700.00", "710.00"), payload[0], row.replace("700.00", "720.00")]
    parsed = reader.read_records(("\n".join(payload) + "\n").encode("utf-8"))
    assert parsed.duplicate_headers == 1
    assert len(parsed.records) == 3


def test_column_count_mismatch_goes_to_quarantine():
    data = ("hotel_code,hotel_name,channel,biz_date,rooms_available,rooms_sold,adr,"
            "revenue_net,updated_at\n"
            "H01,蓝湾·滨海,ota,2026-11-01,180,120,700.00,84000.00,2026-11-02T06:00:00+08:00,多余\n"
            "H01,蓝湾·滨海,ota,2026-11-02,180,121,700.00,84700.00,2026-11-03T06:00:00+08:00\n")
    result = service.ingest(data.encode("utf-8"), "inline.csv", _settings())
    assert len(result.rows) == 1
    assert result.rejects[0]["code"] == "Q_COLUMN_COUNT"
    assert result.rejects[0]["line_no"] == 2


def test_empty_header_only_state():
    # TRAP-18：仅表头与全空行、0 行是三种结论
    data = (SAMPLE_DIR / "daily_metrics_header_only.csv").read_bytes()
    with pytest.raises(EmptyDataset) as excinfo:
        service.ingest(data, "header.csv", _settings())
    assert excinfo.value.code == "E_HEADER_ONLY"
    assert excinfo.value.extra["empty_state"] == "header_only"


def test_empty_all_blank_state():
    # TRAP-18：只有逗号的全空行不能被当成“成功导入 0 行”
    data = (SAMPLE_DIR / "daily_metrics_blank.csv").read_bytes()
    with pytest.raises(EmptyDataset) as excinfo:
        service.ingest(data, "blank.csv", _settings())
    assert excinfo.value.code == "E_ALL_BLANK"
    assert excinfo.value.extra["empty_state"] == "all_blank"


def test_empty_no_rows_state():
    # TRAP-18：没有任何记录时给第三种结论，不与仅表头混用
    with pytest.raises(EmptyDataset) as excinfo:
        service.ingest(b"\n\n", "empty.csv", _settings())
    assert excinfo.value.code == "E_NO_ROWS"
    assert excinfo.value.extra["empty_state"] == "no_rows"


def test_truncated_tail_rejected():
    # TRAP-16 类：末行被截断，不能当有效行
    data = (SAMPLE_DIR / "daily_metrics_truncated.csv").read_bytes()
    with pytest.raises(TruncatedInput) as excinfo:
        service.ingest(data, "truncated.csv", _settings())
    assert excinfo.value.code == "E_TRUNCATED"
    assert excinfo.value.extra["line_no"] == 32


def test_missing_required_columns_rejected():
    data = "hotel_code,channel,biz_date\nH01,ota,2026-11-01\n".encode("utf-8")
    with pytest.raises(BadInput) as excinfo:
        service.ingest(data, "bad.csv", _settings())
    assert excinfo.value.code == "E_MISSING_COLUMNS"
    assert "rooms_sold" in excinfo.value.extra["missing"]


def test_alias_header_maps_to_canonical_columns():
    mapping, missing, unknown, duplicated = columns.map_columns(
        ["门店编码", "hotel_name", "渠道", "biz_date", "可售房晚", "已售房晚",
         "平均房价", "净收入", "更新时间", "备注列"])
    assert missing == []
    assert mapping["hotel_code"] == 0
    assert mapping["rooms_sold"] == 5
    assert unknown == ["备注列"]
    assert duplicated == []


def test_full_last_line_without_newline_is_accepted():
    # TRAP-16 边界：末行没有换行符但列数齐全，是完整行，不是截断
    header = ("hotel_code,hotel_name,channel,biz_date,rooms_available,rooms_sold,adr,"
              "revenue_net,updated_at")
    row = "H01,蓝湾·滨海,ota,2026-11-01,180,120,700.00,84000.00,2026-11-02T06:00:00+08:00"
    data = (header + "\n" + row).encode("utf-8")
    result = service.ingest(data, "tail.csv", _settings())
    assert result.total_records == 1
    assert len(result.rows) == 1
    assert result.rejects == []
