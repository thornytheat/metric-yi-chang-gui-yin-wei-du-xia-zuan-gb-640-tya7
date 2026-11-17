"""SQLite 持久化：运行清单、指标点、隔离行。

每次操作单独开连接，避免 TestClient 多线程共享连接；写入按 run_id 覆盖，
同源重跑命中同一 run_id，先删后写保证行数不变。
"""
from __future__ import annotations

import json
import os
import sqlite3
from decimal import Decimal

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    run_key       TEXT UNIQUE NOT NULL,
    input_name    TEXT NOT NULL,
    input_sha256  TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    duration_ms   INTEGER NOT NULL,
    empty_state   TEXT NOT NULL DEFAULT '',
    manifest      TEXT NOT NULL,
    summary       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS points (
    run_id          TEXT NOT NULL,
    hotel_code      TEXT NOT NULL,
    hotel_name      TEXT NOT NULL,
    channel         TEXT NOT NULL,
    biz_date        TEXT NOT NULL,
    stratum         TEXT NOT NULL,
    rooms_available INTEGER NOT NULL,
    rooms_sold      INTEGER NOT NULL,
    adr             TEXT NOT NULL,
    revenue_net     TEXT NOT NULL,
    late_days       INTEGER NOT NULL,
    late_flag       INTEGER NOT NULL,
    out_of_window   INTEGER NOT NULL,
    source_line     INTEGER NOT NULL,
    updated_utc     TEXT NOT NULL,
    PRIMARY KEY (run_id, hotel_code, channel, biz_date)
);
CREATE TABLE IF NOT EXISTS quarantine (
    run_id     TEXT NOT NULL,
    line_no    INTEGER NOT NULL,
    code       TEXT NOT NULL,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_points_run ON points(run_id);
CREATE INDEX IF NOT EXISTS idx_quarantine_run ON quarantine(run_id);
"""

POINT_FIELDS = (
    "hotel_code", "hotel_name", "channel", "biz_date", "stratum",
    "rooms_available", "rooms_sold", "adr", "revenue_net",
    "late_days", "late_flag", "out_of_window", "source_line", "updated_utc",
)


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- runs -------------------------------------------------------
    def save_run(self, manifest: dict, run_key: str, summary: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, run_key, input_name, input_sha256, rules_version,"
                " created_at, duration_ms, empty_state, manifest, summary)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(run_id) DO UPDATE SET duration_ms=excluded.duration_ms,"
                " manifest=excluded.manifest, summary=excluded.summary",
                (
                    manifest["run_id"], run_key, manifest["input_name"],
                    manifest["input_sha256"], manifest["rules_version"],
                    manifest["created_at"], int(manifest["duration_ms"]),
                    manifest.get("empty_state", ""),
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                    json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str),
                ),
            )

    def run_id_for_key(self, run_key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT run_id FROM runs WHERE run_key=?", (run_key,)).fetchone()
        return row["run_id"] if row else None

    def get_run(self, run_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        return {
            "run_id": row["run_id"],
            "run_key": row["run_key"],
            "input_name": row["input_name"],
            "input_sha256": row["input_sha256"],
            "rules_version": row["rules_version"],
            "created_at": row["created_at"],
            "duration_ms": row["duration_ms"],
            "empty_state": row["empty_state"],
            "manifest": json.loads(row["manifest"]),
            "summary": json.loads(row["summary"]),
        }

    def latest_run(self) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT run_id FROM runs WHERE empty_state='' ORDER BY created_at DESC, rowid DESC"
                " LIMIT 1").fetchone()
        return self.get_run(row["run_id"]) if row else None

    def list_runs(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT run_id FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,)).fetchall()
        return [self.get_run(row["run_id"]) for row in rows]

    # ---- points -----------------------------------------------------
    def replace_points(self, run_id: str, points: list[dict]) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM points WHERE run_id=?", (run_id,))
            conn.executemany(
                "INSERT INTO points (run_id, " + ", ".join(POINT_FIELDS) + ")"
                " VALUES (" + ",".join(["?"] * (len(POINT_FIELDS) + 1)) + ")",
                [tuple([run_id] + [p[f] for f in POINT_FIELDS]) for p in points],
            )

    def load_points(self, run_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM points WHERE run_id=? ORDER BY hotel_code, channel, biz_date",
                (run_id,)).fetchall()
        out = []
        for row in rows:
            item = {field: row[field] for field in POINT_FIELDS}
            item["adr"] = Decimal(item["adr"])
            item["revenue_net"] = Decimal(item["revenue_net"])
            item["late_flag"] = bool(item["late_flag"])
            item["out_of_window"] = bool(item["out_of_window"])
            out.append(item)
        return out

    # ---- quarantine -------------------------------------------------
    def replace_quarantine(self, run_id: str, entries: list[dict]) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM quarantine WHERE run_id=?", (run_id,))
            conn.executemany(
                "INSERT INTO quarantine (run_id, line_no, code, payload) VALUES (?,?,?,?)",
                [(run_id, int(e.get("line_no", 0)), e["code"],
                  json.dumps(e, ensure_ascii=False, sort_keys=True, default=str))
                 for e in entries],
            )

    def load_quarantine(self, run_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT payload FROM quarantine WHERE run_id=? ORDER BY line_no, code",
                (run_id,)).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def point_count(self, run_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM points WHERE run_id=?",
                               (run_id,)).fetchone()
        return int(row["n"])

    def quarantine_count(self, run_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM quarantine WHERE run_id=?",
                               (run_id,)).fetchone()
        return int(row["n"])
