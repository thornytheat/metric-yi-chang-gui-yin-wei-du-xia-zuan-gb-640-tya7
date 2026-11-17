"""测试固定装置：临时库、样例字节、一次真实批次、检测结果。

时间断言全部用固定评估日 2026-11-22，不读 datetime.now()。
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config                                   # noqa: E402
from app.main import create_app                          # noqa: E402
from app.modules.m4_detect import service as detect_service  # noqa: E402
from app.modules.m5_attrib import service as batch_service  # noqa: E402
from app.modules.m7_review import service as review_service  # noqa: E402
from app.store import Store                              # noqa: E402

SAMPLE_DIR = ROOT / "sample_data"
EVAL_DATE = dt.date(2026, 11, 22)
DIRTY = "daily_metrics_dirty.csv"
REVIEW = "review_marginal.csv"


@pytest.fixture()
def settings(tmp_path) -> config.Settings:
    return config.Settings(
        db_path=str(tmp_path / "anomalyattr.sqlite"),
        sample_dir=str(SAMPLE_DIR),
        eval_date=EVAL_DATE,
        seed_on_start=False,
    )


@pytest.fixture()
def store(settings) -> Store:
    return Store(settings.db_path)


@pytest.fixture()
def dirty_bytes() -> bytes:
    return (SAMPLE_DIR / DIRTY).read_bytes()


@pytest.fixture()
def run(settings, store, dirty_bytes) -> dict:
    return batch_service.run_batch(settings, store, dirty_bytes, DIRTY)


@pytest.fixture()
def points(settings, store, run) -> list[dict]:
    return batch_service.load_view(store, run["run_id"])


@pytest.fixture()
def detection(settings, points) -> dict:
    return detect_service.detect(points, settings)


@pytest.fixture()
def attribution(settings, store, run) -> dict:
    return batch_service.attribution(settings, store, run["run_id"], "hotel_code", 7, 7)


@pytest.fixture()
def review_bytes() -> bytes:
    return (SAMPLE_DIR / REVIEW).read_bytes()


@pytest.fixture()
def review_run(settings, store, review_bytes) -> dict:
    return batch_service.run_batch(settings, store, review_bytes, REVIEW)


@pytest.fixture()
def review(settings, store, review_run) -> dict:
    return review_service.review(settings, store, review_run["run_id"])


@pytest.fixture()
def review_client(settings, review_run):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def client(settings, run):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
