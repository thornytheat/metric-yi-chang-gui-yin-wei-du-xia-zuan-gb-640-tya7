"""指标接入入口：样例清单、仅采信预览、整批导入。"""
from __future__ import annotations

import os

from fastapi import APIRouter, File, Form, Request, UploadFile

from app import config, errors, manifest, reasons
from app.modules.m1_ingest import service
from app.modules.m5_attrib import service as batch_service
from app.web import state

router = APIRouter()
MAX_UPLOAD = 8 * 1024 * 1024


def _sample_path(settings: config.Settings, name: str) -> str:
    if not name or os.path.basename(name) != name:
        raise errors.BadInput("样例名不合法", code="E_BAD_SAMPLE")
    path = os.path.join(settings.sample_dir, name)
    if not os.path.isfile(path):
        raise errors.NotFound("样例不存在：" + name, code="E_SAMPLE_NOT_FOUND")
    return path


@router.get("/api/import/samples")
def list_samples(request: Request):
    app_state = state(request)
    out = []
    for name in sorted(os.listdir(app_state.settings.sample_dir)):
        path = os.path.join(app_state.settings.sample_dir, name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as handle:
            data = handle.read()
        out.append({
            "name": name,
            "bytes": len(data),
            "sha256": manifest.sha256_bytes(data),
        })
    return {"samples": out, "default": app_state.settings.default_sample}


@router.post("/api/import/preview")
async def preview_import(
    request: Request,
    file: UploadFile | None = File(default=None),
    sample: str | None = Form(default=None),
):
    app_state = state(request)
    data, name = await _read_input(app_state.settings, file, sample)
    result = service.ingest(data, name, app_state.settings)
    return {
        "input_name": result.input_name,
        "sha256": result.sha256,
        "bytes": result.size_bytes,
        "encoding": result.encoding,
        "header": result.header,
        "mapping": result.mapping,
        "unknown_columns": result.unknown_columns,
        "duplicated_columns": result.duplicated_columns,
        "total_records": result.total_records,
        "accepted_rows": len(result.rows),
        "rejected_rows": len(result.rejects),
        "duplicate_headers": result.duplicate_headers,
        "notices": result.notices,
        "rejects": result.rejects[:20],
        "sample_rows": result.rows[:5],
        "reason_labels": {code: reasons.label(code) for code in
                          {entry["code"] for entry in result.notices + result.rejects}},
    }


@router.post("/api/import")
async def run_import(
    request: Request,
    file: UploadFile | None = File(default=None),
    sample: str | None = Form(default=None),
):
    app_state = state(request)
    data, name = await _read_input(app_state.settings, file, sample)
    summary = batch_service.run_batch(app_state.settings, app_state.store, data, name)
    return summary


async def _read_input(settings: config.Settings, file: UploadFile | None,
                      sample: str | None) -> tuple[bytes, str]:
    if file is not None and file.filename:
        data = await file.read()
        if len(data) > MAX_UPLOAD:
            raise errors.BadInput("文件超过 8MB 上限", code="E_FILE_TOO_LARGE")
        return data, os.path.basename(file.filename)
    name = sample or settings.default_sample
    path = _sample_path(settings, name)
    with open(path, "rb") as handle:
        return handle.read(), name
