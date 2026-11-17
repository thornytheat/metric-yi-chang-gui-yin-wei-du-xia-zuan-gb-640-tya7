"""端到端：脏样例 → 隔离 → 对账 → 检测 → 归因 → 导出，外加 4xx 与页面可达。"""
from __future__ import annotations

import json

import pytest

from app import config
from app.modules.m5_attrib import service as batch_service


def test_full_pipeline_end_to_end(client, run):
    accounts = client.get("/api/quarantine").json()["accounts"]
    assert accounts["rows_read"] == 1016
    assert accounts["rows_quarantine"] == 11
    assert accounts["points_missing"] == 2

    anomalies = client.get("/api/anomalies").json()
    assert anomalies["summary"]["events"] == 2
    confirmed = [item for item in anomalies["detections"] if item["status"] == "confirmed"]
    assert len(confirmed) == 4

    attribution = client.get("/api/attribution").json()
    assert attribution["identity_ok"] is True
    assert attribution["share_sum"] == "1.0000"

    reconcile = client.get("/api/reconcile").json()
    assert reconcile["identity_ok"] is True
    assert reconcile["unknown_reason_codes"] == []

    report = client.get("/api/report").json()
    assert report["accounts"]["revenue_accepted"] == run["accounts"]["revenue_accepted"]
    assert report["attribution"]["total"]["residual"] == "0.00"

    manifest = json.loads(client.get("/api/export/manifest.json").text)
    assert manifest["manifest"]["input_sha256"] == run["input_sha256"]

    csv_text = client.get("/api/export/points.csv").text
    assert len(csv_text.splitlines()) == accounts["points_valid"] + 1


def test_empty_state_returns_4xx_with_state(client):
    for sample, expected_code in (
        ("daily_metrics_header_only.csv", "E_HEADER_ONLY"),
        ("daily_metrics_blank.csv", "E_ALL_BLANK"),
    ):
        response = client.post("/api/import", data={"sample": sample})
        assert response.status_code == 422, response.text
        body = response.json()
        assert body["error"] == expected_code
        assert body["empty_state"] in ("header_only", "all_blank")


def test_truncated_upload_returns_4xx(client):
    response = client.post("/api/import", data={"sample": "daily_metrics_truncated.csv"})
    assert response.status_code == 400
    assert response.json()["error"] == "E_TRUNCATED"


def test_unknown_sample_returns_4xx(client):
    response = client.post("/api/import", data={"sample": "not-there.csv"})
    assert response.status_code == 404
    assert response.json()["error"] == "E_SAMPLE_NOT_FOUND"


def test_rerun_via_api_is_idempotent(client, run):
    first = client.post("/api/attribution/rerun", json={}).json()
    second = client.post("/api/attribution/rerun", json={}).json()
    assert first["run_id"] == run["run_id"] == second["run_id"]
    assert first["reused"] is True and second["reused"] is True
    assert first["accounts"] == second["accounts"]


def test_all_pages_render(client):
    for path in ("/", "/anomalies", "/attribution", "/drilldown", "/batches", "/report"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "text/html" in response.headers["content-type"]
    index = client.get("/").text
    assert "水位" in index or "批次" in index
    anomalies = client.get("/anomalies").text
    assert "H02/ota" in anomalies


def test_preview_endpoint_reports_diagnostics(client):
    response = client.post("/api/import/preview",
                           data={"sample": "daily_metrics_dirty.csv"})
    body = response.json()
    assert body["encoding"] == "utf-8-sig"
    assert body["total_records"] == 1016
    assert body["accepted_rows"] == 1015
    assert "Q_COLUMN_COUNT" in {item["code"] for item in body["rejects"]}


def test_detection_insufficient_before_baseline(settings, points):
    # 端到端条件下仍保留“基线不足不判”的边界
    from app.modules.m4_detect import service as detect_service

    result = detect_service.detect(points, settings)
    assert result["summary"]["insufficient_points"] == 336
    assert all(item["z"] is None for item in result["detections"]
               if item["status"] == "insufficient")


def test_reconcile_without_run_is_404(settings):
    from fastapi.testclient import TestClient
    from app.main import create_app

    app = create_app(config.Settings(db_path=settings.db_path + ".empty",
                                     sample_dir=settings.sample_dir,
                                     seed_on_start=False))
    with TestClient(app) as client:
        assert client.get("/api/reconcile").status_code == 404
        assert client.get("/api/quarantine").status_code == 404
        assert client.get("/").status_code == 200
