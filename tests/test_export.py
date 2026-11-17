"""M6 报告与导出：CSV 证据列、清单文件、报告四账、未知类型 4xx。"""
from __future__ import annotations

import json

from app.modules.m6_export import service as export_service


def test_points_csv_has_bom_and_header(run, store):
    text = export_service.export_text(
        __import__("app.config", fromlist=["Settings"]).Settings(
            db_path=store.db_path, seed_on_start=False),
        store, "points.csv", run["run_id"])[0]
    assert text.startswith("\ufeff")
    header = text.splitlines()[0].replace("\ufeff", "").split(",")
    assert header[:4] == ["biz_date", "hotel_code", "hotel_name", "channel"]
    assert len(text.splitlines()) == run["accounts"]["points_valid"] + 1


def test_export_unknown_kind_is_4xx(client):
    response = client.get("/api/export/nope.csv")
    assert response.status_code == 400
    assert response.json()["error"] == "E_BAD_EXPORT"


def test_manifest_json_contains_sha_and_rules(client, run):
    response = client.get(f"/api/export/manifest.json?run_id={run['run_id']}")
    assert response.status_code == 200
    payload = json.loads(response.text)
    assert payload["manifest"]["input_sha256"] == run["input_sha256"]
    assert payload["rules_version"] == run["rules_version"]
    assert payload["accounts"]["points_valid"] == run["accounts"]["points_valid"]


def test_anomalies_csv_matches_api(client, run):
    response = client.get(f"/api/export/anomalies.csv?run_id={run['run_id']}")
    lines = response.text.splitlines()
    assert lines[0].replace("\ufeff", "").startswith("biz_date,hotel_code,channel")
    confirmed = [line for line in lines[1:] if ",confirmed," in line]
    api = client.get("/api/anomalies?status=confirmed").json()
    assert len(confirmed) == len(api["detections"]) == 4


def test_attribution_csv_columns(client, run):
    response = client.get(f"/api/export/attribution.csv?run_id={run['run_id']}")
    header = response.text.splitlines()[0].replace("\ufeff", "").split(",")
    assert header == ["key", "label", "rev_base", "rev_cur", "delta", "share",
                      "rooms_delta", "occ_delta", "adr_delta", "ok"]
    assert len(response.text.splitlines()) == 5


def test_quarantine_csv_endpoint(client, run):
    response = client.get("/api/quarantine.csv")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert "Q_DUP_CONFLICT" in response.text


def test_report_payload_has_four_accounts(client):
    payload = client.get("/api/report").json()
    assert payload["accounts"]["rows_read"] == 1016
    assert [check["name"] for check in payload["checks"]] == ["行账", "点账"]
    assert payload["detection"]["events"] == 2
    assert payload["attribution"]["total"]["residual"] == "0.00"
    assert payload["reconcile_ok"] is True


def test_report_events_and_reason_counts(client):
    payload = client.get("/api/report").json()
    assert len(payload["events"]) == 2
    codes = {row["code"] for row in payload["reason_counts"]}
    assert {"Q_SENTINEL", "Q_DUP_CONFLICT", "D_SUPERSEDED"} <= codes
    assert payload["run"]["encoding"] == "utf-8-sig"


def test_export_requires_run(client):
    response = client.get("/api/report?run_id=run-missing")
    assert response.status_code == 404
    assert response.json()["error"] == "E_NO_RUN"
