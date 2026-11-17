"""SQLite 持久化：运行清单、指标点、隔离行、退网清除凭据。

每次操作单独开连接，避免 TestClient 多线程共享连接；写入按 run_id 覆盖，
同源重跑命中同一 run_id，先删后写保证行数不变。

M8 退网清除另开三张表：purges 记不可逆清除任务（门店标识只留在任务头，
供审计核对），purge_point_vouchers 与 purge_quarantine_vouchers 承接原账，
只按 run × 渠道 × 业务日聚合，不含任何门店标识。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
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
CREATE TABLE IF NOT EXISTS purges (
    purge_id        TEXT PRIMARY KEY,
    hotel_code      TEXT NOT NULL,
    state           TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    completed_at    TEXT NOT NULL DEFAULT '',
    report          TEXT NOT NULL DEFAULT '{}',
    progress        TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_purges_hotel ON purges(hotel_code);
CREATE TABLE IF NOT EXISTS purge_point_vouchers (
    purge_id        TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    channel         TEXT NOT NULL,
    biz_date        TEXT NOT NULL,
    point_count     INTEGER NOT NULL,
    rooms_available INTEGER NOT NULL,
    rooms_sold      INTEGER NOT NULL,
    revenue_net     TEXT NOT NULL,
    PRIMARY KEY (purge_id, run_id, channel, biz_date)
);
CREATE TABLE IF NOT EXISTS purge_quarantine_vouchers (
    purge_id   TEXT NOT NULL,
    run_id     TEXT NOT NULL,
    code       TEXT NOT NULL,
    entry_count INTEGER NOT NULL,
    PRIMARY KEY (purge_id, run_id, code)
);
CREATE INDEX IF NOT EXISTS idx_points_run ON points(run_id);
CREATE INDEX IF NOT EXISTS idx_quarantine_run ON quarantine(run_id);
CREATE INDEX IF NOT EXISTS idx_purge_point_purge ON purge_point_vouchers(purge_id);
CREATE INDEX IF NOT EXISTS idx_purge_quarantine_purge ON purge_quarantine_vouchers(purge_id);
"""

POINT_FIELDS = (
    "hotel_code", "hotel_name", "channel", "biz_date", "stratum",
    "rooms_available", "rooms_sold", "adr", "revenue_net",
    "late_days", "late_flag", "out_of_window", "source_line", "updated_utc",
)


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        # 进程内互斥：清除与导入在同一 SQLite 上串行，清除优先（导入先让锁）。
        self.write_lock = threading.RLock()
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

    # ---- M8 退网清除 ------------------------------------------------
    def create_purge(self, purge_id: str, hotel_code: str, now_stamp: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO purges (purge_id, hotel_code, state, created_at, updated_at)"
                " VALUES (?,?,?,?,?)",
                (purge_id, hotel_code, "running", now_stamp, now_stamp))

    def get_purge_by_hotel(self, hotel_code: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM purges WHERE hotel_code=?",
                               (hotel_code,)).fetchone()
        return self._purge_row(row)

    def get_purge(self, purge_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM purges WHERE purge_id=?",
                               (purge_id,)).fetchone()
        return self._purge_row(row)

    def list_purges(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM purges ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,)).fetchall()
        return [self._purge_row(row) for row in rows]

    def purge_running(self) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM purges WHERE state='running' LIMIT 1").fetchone()
        return self._purge_row(row)

    def decommissioned_hotels(self) -> set[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT hotel_code FROM purges WHERE state='done'").fetchall()
        return {row["hotel_code"] for row in rows}

    def update_purge_state(self, purge_id: str, state: str, now_stamp: str,
                           report: dict | None = None, completed: bool = False) -> None:
        with self._conn() as conn:
            if completed:
                conn.execute(
                    "UPDATE purges SET state=?, updated_at=?, completed_at=?, report=?"
                    " WHERE purge_id=?",
                    (state, now_stamp, now_stamp,
                     json.dumps(report or {}, ensure_ascii=False, sort_keys=True), purge_id))
            else:
                conn.execute(
                    "UPDATE purges SET state=?, updated_at=? WHERE purge_id=?",
                    (state, now_stamp, purge_id))

    def save_purge_progress(self, purge_id: str, run_reports: list[dict]) -> None:
        """每个批次事务提交后落一次进度，崩溃恢复用它拿回清除前账目快照。"""
        with self._conn() as conn:
            conn.execute("UPDATE purges SET progress=? WHERE purge_id=?",
                         (json.dumps({"runs": run_reports}, ensure_ascii=False,
                                     sort_keys=True, default=str), purge_id))

    def load_purge_progress(self, purge_id: str) -> list[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT progress FROM purges WHERE purge_id=?",
                               (purge_id,)).fetchone()
        if not row or not row["progress"]:
            return []
        return json.loads(row["progress"]).get("runs", [])

    def load_point_vouchers(self, purge_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM purge_point_vouchers WHERE purge_id=?"
                " ORDER BY run_id, channel, biz_date", (purge_id,)).fetchall()
        return [dict(row) for row in rows]

    def load_quarantine_vouchers(self, purge_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM purge_quarantine_vouchers WHERE purge_id=?"
                " ORDER BY run_id, code", (purge_id,)).fetchall()
        return [dict(row) for row in rows]

    def points_for_hotel(self, run_id: str, hotel_code: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM points WHERE run_id=? AND hotel_code=?"
                " ORDER BY channel, biz_date", (run_id, hotel_code)).fetchall()
        out = []
        for row in rows:
            item = {field: row[field] for field in POINT_FIELDS}
            out.append(item)
        return out

    def quarantine_for_hotel(self, run_id: str, hotel_code: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT payload FROM quarantine WHERE run_id=?"
                " ORDER BY line_no, code", (run_id,)).fetchall()
        entries = [json.loads(row["payload"]) for row in rows]
        return [entry for entry in entries if entry.get("hotel_code") == hotel_code]

    def all_run_ids(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT run_id FROM runs ORDER BY created_at, rowid").fetchall()
        return [row["run_id"] for row in rows]

    def purge_run_in_transaction(self, run_id: str, hotel_code: str, purge_id: str,
                                 point_vouchers: list[dict],
                                 quarantine_vouchers: list[dict],
                                 summary: dict) -> dict:
        """单个批次的清除在一个 SQLite 事务内完成：凭据写入 → 明细删除 → 账目复算。

        任一步失败整体回滚，读路径绝不会看到“删了一半”的批次；
        中断恢复时若该批次凭据已存在，则视为本批次已提交，直接跳过。
        """
        with self._conn() as conn:
            done = conn.execute(
                "SELECT 1 FROM purge_point_vouchers WHERE purge_id=? AND run_id=? LIMIT 1",
                (purge_id, run_id)).fetchone()
            if not done:
                done = conn.execute(
                    "SELECT 1 FROM purge_quarantine_vouchers WHERE purge_id=? AND run_id=?"
                    " LIMIT 1", (purge_id, run_id)).fetchone()
            if done:
                return {"skipped": True}
            conn.executemany(
                "INSERT INTO purge_point_vouchers (purge_id, run_id, channel, biz_date,"
                " point_count, rooms_available, rooms_sold, revenue_net)"
                " VALUES (?,?,?,?,?,?,?,?)",
                [(item["purge_id"], item["run_id"], item["channel"], item["biz_date"],
                  int(item["point_count"]), int(item["rooms_available"]),
                  int(item["rooms_sold"]), str(item["revenue_net"]))
                 for item in point_vouchers],
            )
            conn.executemany(
                "INSERT INTO purge_quarantine_vouchers (purge_id, run_id, code, entry_count)"
                " VALUES (?,?,?,?)",
                [(item["purge_id"], item["run_id"], item["code"], int(item["entry_count"]))
                 for item in quarantine_vouchers],
            )
            removed_points = conn.execute(
                "DELETE FROM points WHERE run_id=? AND hotel_code=?",
                (run_id, hotel_code)).rowcount
            # 直接按 payload 内的门店编码过滤，不依赖 rowid 列表（空列表的 NOT IN 会误删全表）。
            q_rows = conn.execute(
                "SELECT payload FROM quarantine WHERE run_id=?",
                (run_id,)).fetchall()
            before_q = len(q_rows)
            conn.execute(
                "DELETE FROM quarantine WHERE run_id=? AND json_extract(payload, '$.hotel_code')=?",
                (run_id, hotel_code))
            removed_quarantine = before_q - conn.execute(
                "SELECT COUNT(*) AS n FROM quarantine WHERE run_id=?",
                (run_id,)).fetchone()["n"]
            conn.execute("UPDATE runs SET summary=? WHERE run_id=?",
                         (json.dumps(summary, ensure_ascii=False, sort_keys=True,
                                     default=str), run_id))
        return {"skipped": False, "removed_points": removed_points,
                "removed_quarantine": removed_quarantine}

    @staticmethod
    def _purge_row(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {
            "purge_id": row["purge_id"],
            "hotel_code": row["hotel_code"],
            "state": row["state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
            "report": json.loads(row["report"] or "{}"),
        }
