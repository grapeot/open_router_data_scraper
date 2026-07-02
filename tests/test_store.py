"""Unit tests for SQLite Storage — dedup, charts, snapshots."""
from __future__ import annotations

import json
import pytest

from open_router_data_scraper.client import ActivityRow, ChartPoint
from open_router_data_scraper.store import Storage


def _act(date="2026-07-02 00:00:00", slug="z-ai/glm-5.2-20260616", variant="standard", count=1000):
    return ActivityRow(
        date=date, model_permaslug=slug, variant=variant,
        total_prompt_tokens=100_000, total_completion_tokens=5_000,
        total_native_tokens_reasoning=1_000, total_native_tokens_cached=80_000,
        count=count, total_tool_calls=200, requests_with_tool_call_errors=5,
        num_media_prompt=0, num_media_completion=0, image_output_requests=0,
        num_video_prompt=0, video_output_seconds=0, rerank_documents=0,
        stt_transcript_characters=0, num_audio_prompt=0, variant_permaslug=slug,
    )


@pytest.fixture
def store(tmp_path):
    s = Storage(db_path=tmp_path / "test.db")
    yield s
    s.close()


class TestUpsertActivity:
    def test_insert(self, store):
        assert store.upsert_activity([_act()]) == 1

    def test_dedup(self, store):
        store.upsert_activity([_act()])
        assert store.upsert_activity([_act()]) == 0

    def test_different_variant(self, store):
        assert store.upsert_activity([_act(variant="standard"), _act(variant="nitro")]) == 2

    def test_empty(self, store):
        assert store.upsert_activity([]) == 0

    def test_partial_dedup(self, store):
        store.upsert_activity([_act(date="2026-07-01 00:00:00")])
        assert store.upsert_activity([_act(date="2026-07-01 00:00:00"), _act(date="2026-07-02 00:00:00")]) == 1


class TestQueryActivity:
    def test_all(self, store):
        store.upsert_activity([_act(slug="org/a-20260101"), _act(slug="org/b-20260101", date="2026-07-01 00:00:00")])
        assert len(store.query_activity()) == 2

    def test_filter_slug(self, store):
        store.upsert_activity([_act(slug="org/a-20260101"), _act(slug="org/b-20260101")])
        assert len(store.query_activity(slug="a-20260101")) == 1

    def test_filter_date(self, store):
        store.upsert_activity([_act(date="2026-06-01 00:00:00"), _act(date="2026-07-01 00:00:00"), _act(date="2026-08-01 00:00:00")])
        assert len(store.query_activity(date_from="2026-06-15", date_to="2026-07-15")) == 1


class TestDateRange:
    def test_empty(self, store):
        assert store.date_range() == (None, None)

    def test_with_data(self, store):
        store.upsert_activity([_act(date="2026-06-01 00:00:00"), _act(date="2026-07-02 00:00:00")])
        lo, hi = store.date_range()
        assert lo == "2026-06-01 00:00:00"
        assert hi == "2026-07-02 00:00:00"


class TestTrackedModels:
    def test_empty(self, store):
        assert store.tracked_models() == []

    def test_multiple(self, store):
        store.upsert_activity([_act(slug="org/a-20260101"), _act(slug="org/b-20260101")])
        models = store.tracked_models()
        assert len(models) == 2


class TestChartSeries:
    def test_upsert_and_query(self, store):
        points = [ChartPoint(date="2026-07-02", values={"model-a": 100, "model-b": 200})]
        assert store.upsert_chart("rankings_chart", points) == 2
        assert store.upsert_chart("rankings_chart", points) == 0  # dedup
        rows = store.query_chart("rankings_chart")
        assert len(rows) == 2

    def test_different_endpoints(self, store):
        store.upsert_chart("tools", [ChartPoint(date="2026-07-02", values={"m1": 100})])
        store.upsert_chart("images", [ChartPoint(date="2026-07-02", values={"m1": 100})])
        assert len(store.query_chart("tools")) == 1
        assert len(store.query_chart("images")) == 1


class TestSnapshots:
    def test_task_spend(self, store):
        data = {"data": {"spend": {"macroCategories": [{"key": "code", "label": "Code", "spendShare": 0.26}]}}}
        assert store.snapshot_task_spend("2026-07-02", data) == 1
        assert store.snapshot_task_spend("2026-07-02", data) == 0  # dedup
        assert store.table_count("snapshot_task_spend") == 1

    def test_performance(self, store):
        models = [{"slug": "org/model-20260101", "name": "Model", "author": "org", "request_count": 100, "p50_latency": 200, "p50_throughput": 50}]
        assert store.snapshot_performance("2026-07-02", models) == 1
        assert store.table_count("snapshot_performance") == 1

    def test_benchmarks(self, store):
        data = {"data": {"aaData": {"intelligence": [{"uid": "org/m-20260101", "permaslug": "org/m-20260101", "aa_name": "M", "score": 59.9}]}}}
        assert store.snapshot_benchmarks("2026-07-02", data) == 1

    def test_apps(self, store):
        data = {"data": {"day": [{"app_id": 1, "rank": 1, "total_tokens": "1000", "total_requests": 10, "app": {"id": 1}}], "week": [], "month": []}}
        assert store.snapshot_apps("2026-07-02", data) == 1