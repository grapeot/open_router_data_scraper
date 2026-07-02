"""Integration tests for CLI — httpx MockTransport."""
from __future__ import annotations

import sqlite3

import httpx
import pytest

from open_router_data_scraper.cli import main


MOCK_MODELS = {"data": [{"id": "z-ai/glm-5.2", "canonical_slug": "z-ai/glm-5.2-20260616", "name": "GLM 5.2"}]}
MOCK_TOP_MODELS = {"data": {"models": [{"slug": "z-ai/glm-5.2", "permaslug": "z-ai/glm-5.2-20260616", "name": "GLM 5.2"}]}}
MOCK_ACTIVITY_ROW = {
    "date": "2026-07-02 00:00:00", "model_permaslug": "z-ai/glm-5.2-20260616", "variant": "standard",
    "total_completion_tokens": 5500111514, "total_prompt_tokens": 206031346043,
    "total_native_tokens_reasoning": 3235319960, "count": 4947839,
    "num_media_prompt": 0, "num_media_completion": 0, "image_output_requests": 0,
    "num_video_prompt": 0, "video_output_seconds": 0, "rerank_documents": 0,
    "stt_transcript_characters": 0, "num_audio_prompt": 0,
    "total_native_tokens_cached": 162455052418, "total_tool_calls": 2660898,
    "requests_with_tool_call_errors": 35600, "variant_permaslug": "z-ai/glm-5.2-20260616",
}
MOCK_ACTIVITY = {"data": {"analytics": [MOCK_ACTIVITY_ROW]}}
MOCK_RANKINGS_MODELS = {"data": [{**MOCK_ACTIVITY_ROW, "total_native_tokens_reasoning": 0, "total_native_tokens_cached": 0}]}
MOCK_CHART = {"data": {"data": [{"x": "2026-07-02", "ys": {"z-ai/glm-5.2-20260616": 133784000000000}}]}}
MOCK_TOOLS_CHART = {"data": [{"x": "2026-04-06", "ys": {"minimax/minimax-m2.7-20260318": 19760495}}]}
MOCK_TASK_SPEND = {"data": {"spend": {"windowDays": 30, "macroCategories": [{"key": "code", "label": "Code", "spendShare": 0.26}]}}}
MOCK_PERFORMANCE = {"data": [{"slug": "anthropic/claude-sonnet-5-20260630", "name": "Claude Sonnet 5", "author": "anthropic", "request_count": 1523646, "p50_latency": 2087, "p50_throughput": 78}]}
MOCK_BENCHMARKS = {"data": {"aaData": {"intelligence": [{"uid": "anthropic/claude-5-fable-20260609", "permaslug": "anthropic/claude-5-fable-20260609", "aa_name": "Claude Fable 5", "score": 59.9}]}}}
MOCK_APPS = {"data": {"day": [{"app_id": 3067167, "total_tokens": "821486162963", "total_requests": 10972964, "rank": 2, "app": {"id": 3067167}}], "week": [], "month": []}}


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "/api/frontend/v1/models/find" in url:
        return httpx.Response(200, json=MOCK_TOP_MODELS)
    if "/api/v1/models" in url:
        return httpx.Response(200, json=MOCK_MODELS)
    if "/api/frontend/v1/rankings/models" in url:
        return httpx.Response(200, json=MOCK_RANKINGS_MODELS)
    if "/api/frontend/v1/stats/model-activity" in url:
        perma = request.url.params.get("permaslug", "")
        if "nonexistent" in perma:
            return httpx.Response(200, json={"data": {"analytics": []}})
        return httpx.Response(200, json=MOCK_ACTIVITY)
    if "/api/frontend/v1/rankings/model-rankings-chart" in url:
        return httpx.Response(200, json=MOCK_CHART)
    if "/api/frontend/v1/rankings/tools" in url:
        return httpx.Response(200, json=MOCK_TOOLS_CHART)
    if "/api/frontend/v1/rankings/task-spend" in url:
        return httpx.Response(200, json=MOCK_TASK_SPEND)
    if "/api/frontend/v1/rankings/performance" in url:
        return httpx.Response(200, json=MOCK_PERFORMANCE)
    if "/api/frontend/v1/rankings/benchmarks" in url:
        return httpx.Response(200, json=MOCK_BENCHMARKS)
    if "/api/frontend/v1/rankings/apps" in url:
        return httpx.Response(200, json=MOCK_APPS)
    if "/api/frontend/v1/rankings/" in url:
        return httpx.Response(200, json={"data": [{"x": "2026-07-02", "ys": {"m": 100}}]})
    return httpx.Response(200, json={"data": {}})


@pytest.fixture
def mock_transport(monkeypatch):
    transport = httpx.MockTransport(_handler)
    original_init = httpx.Client.__init__
    def patched_init(self, **kw):
        kw.pop("transport", None)
        original_init(self, transport=transport, **kw)
    monkeypatch.setattr(httpx.Client, "__init__", patched_init)


class TestDiscover:
    def test_top_models(self, mock_transport, capsys):
        assert main(["discover", "--top", "1"]) == 0
        assert "z-ai/glm-5.2" in capsys.readouterr().out

class TestFetch:
    def test_canonical(self, mock_transport, capsys):
        assert main(["fetch", "z-ai/glm-5.2-20260616"]) == 0
        assert "2026-07-02" in capsys.readouterr().out

    def test_auto_resolve(self, mock_transport, capsys):
        assert main(["fetch", "z-ai/glm-5.2"]) == 0

    def test_no_data(self, mock_transport, capsys):
        assert main(["fetch", "nonexistent/model-20260101"]) == 1

class TestArchive:
    def test_two_layer(self, mock_transport, tmp_path):
        db = tmp_path / "test.db"
        assert main(["archive", "--top", "1", "--db", str(db)]) == 0
        conn = sqlite3.connect(str(db))
        # batch: 1 row, supplement: 1 row (same PK, dedup'd)
        count = conn.execute("SELECT COUNT(*) FROM model_activity").fetchone()[0]
        assert count == 1  # same slug+date+variant, dedup
        conn.close()

    def test_dedup(self, mock_transport, tmp_path):
        db = tmp_path / "test.db"
        main(["archive", "--top", "1", "--db", str(db)])
        main(["archive", "--top", "1", "--db", str(db)])
        conn = sqlite3.connect(str(db))
        assert conn.execute("SELECT COUNT(*) FROM model_activity").fetchone()[0] == 1
        conn.close()

class TestSnapshot:
    def test_all_snapshots(self, mock_transport, tmp_path):
        db = tmp_path / "test.db"
        assert main(["snapshot", "--db", str(db)]) == 0
        conn = sqlite3.connect(str(db))
        assert conn.execute("SELECT COUNT(*) FROM chart_series").fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM snapshot_task_spend").fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM snapshot_performance").fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM snapshot_benchmarks").fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM snapshot_apps").fetchone()[0] > 0
        conn.close()

class TestQuery:
    def test_from_db(self, mock_transport, tmp_path, capsys):
        db = tmp_path / "test.db"
        main(["archive", "--top", "1", "--db", str(db)])
        assert main(["query", "--slug", "glm-5.2", "--db", str(db)]) == 0

    def test_empty(self, tmp_path, capsys):
        assert main(["query", "--db", str(tmp_path / "empty.db")]) == 1

class TestModels:
    def test_lists(self, mock_transport, tmp_path, capsys):
        db = tmp_path / "test.db"
        main(["archive", "--top", "1", "--db", str(db)])
        assert main(["models", "--db", str(db)]) == 0

    def test_empty(self, tmp_path, capsys):
        assert main(["models", "--db", str(tmp_path / "empty.db")]) == 1