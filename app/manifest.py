"""运行清单：run_id、输入摘要、规则版本、参数与耗时。

run_id 由「输入 sha256 + 规则版本 + 参数摘要」确定性派生，同源重跑得到同一个 id。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def params_digest(params: dict) -> str:
    payload = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def make_run_id(input_sha256: str, rules_version: str, params_key: str) -> str:
    seed = f"{input_sha256}|{rules_version}|{params_key}"
    return "run-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


@dataclass
class RunClock:
    started: float = field(default_factory=time.monotonic)

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)


@dataclass
class RunManifest:
    run_id: str
    input_name: str
    input_sha256: str
    input_bytes: int
    rules_version: str
    params_key: str
    created_at: str
    duration_ms: int
    counts: dict
    encoding: str
    empty_state: str = ""

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "input_name": self.input_name,
            "input_sha256": self.input_sha256,
            "input_bytes": self.input_bytes,
            "rules_version": self.rules_version,
            "params_key": self.params_key,
            "created_at": self.created_at,
            "duration_ms": self.duration_ms,
            "counts": dict(self.counts),
            "encoding": self.encoding,
            "empty_state": self.empty_state,
        }
