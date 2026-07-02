"""Unit tests for OpenRouterClient — mock httpx, no real network."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from open_router_data_scraper.client import (
    ActivityRow, ChartPoint, OpenRouterClient,
)


MOCK_MODELS_RESPONSE = {
    "data": [
        {"id": "z-ai/glm-5.2", "canonical_slug": "z-ai/glm-5.2-20260616", "name": "Z.ai: GLM 5.2"},
        {"id": "openai/gpt-5.5", "canonical_slug": "openai/gpt-5.5-20260423", "name": "OpenAI: GPT-5.5"},
    ]
}

def _activity_row(date="2026-07-02 00:00:00", slug="z-ai/glm-5.2-20260616", variant="standard", reasoning=3235319960, cached=162455052418):
    return {
        "date": date, "model_permaslug": slug, "variant": variant,
        "total_completion_tokens": 5500111514, "total_prompt_tokens": 206031346043,
        "total_native_tokens_reasoning": reasoning, "total_native_tokens_cached": cached,
        "count": 4947839, "total_tool_calls": 2660898, "requests_with_tool_call_errors": 35600,
        "num_media_prompt": 0, "num_media_completion": 0, "image_output_requests": 0,
        "num_video_prompt": 0, "video_output_seconds": 0, "rerank_documents": 0,
        "stt_transcript_characters": 0, "num_audio_prompt": 0, "variant_permaslug": slug,
    }

MOCK_ACTIVITY_RESPONSE = {"data": {"analytics": [_activity_row()]}}
MOCK_RANKINGS_MODELS_RESPONSE = {"data": [_activity_row(reasoning=0, cached=0)]}
MOCK_RANKINGS_CHART = {"data": {"data": [{"x": "2026-07-02", "ys": {"z-ai/glm-5.2-20260616": 133784000000000}}]}}
MOCK_TOOLS_CHART = {"data": [{"x": "2026-04-06", "ys": {"minimax/minimax-m2.7-20260318": 19760495}}]}
MOCK_TASK_SPEND = {"data": {"spend": {"windowDays": 30, "macroCategories": [{"key": "code", "label": "Code", "spendShare": 0.26}]}}}
MOCK_PERFORMANCE = {"data": [{"slug": "anthropic/claude-sonnet-5-20260630", "name": "Claude Sonnet 5", "author": "anthropic", "request_count": 1523646, "p50_latency": 2087, "p50_throughput": 78}]}
MOCK_BENCHMARKS = {"data": {"aaData": {"intelligence": [{"uid": "anthropic/claude-5-fable-20260609", "permaslug": "anthropic/claude-5-fable-20260609", "aa_name": "Claude Fable 5", "score": 59.9}]}}}
MOCK_APPS = {"data": {"day": [{"app_id": 3067167, "total_tokens": "821486162963", "total_requests": 10972964, "rank": 2, "app": {"id": 3067167}}], "week": [], "month": []}}
MOCK_TOP_MODELS = {"data": {"models": [{"slug": "z-ai/glm-5.2", "permaslug": "z-ai/glm-5.2-20260616", "name": "Z.ai: GLM 5.2"}]}}


def _mock_resp(json_body, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status.return_value = None
    return resp


@pytest.fixture
def client():
    c = OpenRouterClient()
    yield c
    c.close()


class TestIsCanonical:
    def test_canonical(self):
        assert OpenRouterClient._is_canonical("z-ai/glm-5.2-20260616")
    def test_short(self):
        assert not OpenRouterClient._is_canonical("z-ai/glm-5.2")
    def test_non_date(self):
        assert not OpenRouterClient._is_canonical("openai/gpt-4o")


class TestResolveCanonical:
    def test_already_canonical(self, client):
        with patch.object(client._client, "get") as m:
            assert client.resolve_canonical_slug("z-ai/glm-5.2-20260616") == "z-ai/glm-5.2-20260616"
            m.assert_not_called()

    def test_short_resolves(self, client):
        with patch.object(client._client, "get") as m:
            m.return_value = _mock_resp(MOCK_MODELS_RESPONSE)
            assert client.resolve_canonical_slug("z-ai/glm-5.2") == "z-ai/glm-5.2-20260616"

    def test_unknown_returns_as_is(self, client):
        with patch.object(client._client, "get") as m:
            m.return_value = _mock_resp({"data": []})
            assert client.resolve_canonical_slug("unknown/model") == "unknown/model"


class TestGetModelActivity:
    def test_returns_rows(self, client):
        with patch.object(client._client, "get") as m:
            m.return_value = _mock_resp(MOCK_ACTIVITY_RESPONSE)
            rows = client.get_model_activity("z-ai/glm-5.2-20260616")
            assert len(rows) == 1
            assert isinstance(rows[0], ActivityRow)
            assert rows[0].total_native_tokens_reasoning == 3235319960

    def test_auto_resolve(self, client):
        with patch.object(client._client, "get") as m:
            m.side_effect = [_mock_resp(MOCK_MODELS_RESPONSE), _mock_resp(MOCK_ACTIVITY_RESPONSE)]
            rows = client.get_model_activity("z-ai/glm-5.2")
            assert len(rows) == 1
            assert m.call_count == 2

    def test_empty(self, client):
        with patch.object(client._client, "get") as m:
            m.return_value = _mock_resp({"data": {"analytics": []}})
            assert client.get_model_activity("nonexistent/model-20260101") == []


class TestGetRankingsModels:
    def test_returns_rows(self, client):
        with patch.object(client._client, "get") as m:
            m.return_value = _mock_resp(MOCK_RANKINGS_MODELS_RESPONSE)
            rows = client.get_rankings_models(view="day")
            assert len(rows) == 1
            assert rows[0].total_native_tokens_reasoning == 0  # batch layer has 0


class TestCharts:
    def test_rankings_chart(self, client):
        with patch.object(client._client, "get") as m:
            m.return_value = _mock_resp(MOCK_RANKINGS_CHART)
            points = client.get_rankings_chart()
            assert len(points) == 1
            assert isinstance(points[0], ChartPoint)
            assert "z-ai/glm-5.2-20260616" in points[0].values

    def test_tools_chart(self, client):
        with patch.object(client._client, "get") as m:
            m.return_value = _mock_resp(MOCK_TOOLS_CHART)
            points = client.get_tools_chart()
            assert len(points) == 1
            assert "minimax/minimax-m2.7-20260318" in points[0].values

    def test_modality_chart(self, client):
        with patch.object(client._client, "get") as m:
            m.return_value = _mock_resp(MOCK_RANKINGS_CHART)  # nested shape
            points = client.get_modality_chart("text")
            assert len(points) == 1


class TestSnapshots:
    def test_task_spend(self, client):
        with patch.object(client._client, "get") as m:
            m.return_value = _mock_resp(MOCK_TASK_SPEND)
            data = client.get_task_spend()
            assert "spend" in data["data"]

    def test_performance(self, client):
        with patch.object(client._client, "get") as m:
            m.return_value = _mock_resp(MOCK_PERFORMANCE)
            models = client.get_performance()
            assert len(models) == 1
            assert models[0]["slug"] == "anthropic/claude-sonnet-5-20260630"

    def test_benchmarks(self, client):
        with patch.object(client._client, "get") as m:
            m.return_value = _mock_resp(MOCK_BENCHMARKS)
            data = client.get_benchmarks()
            assert "aaData" in data["data"]

    def test_apps(self, client):
        with patch.object(client._client, "get") as m:
            m.return_value = _mock_resp(MOCK_APPS)
            data = client.get_apps_ranking()
            assert "day" in data["data"]


class TestGetTopModels:
    def test_limit(self, client):
        with patch.object(client._client, "get") as m:
            m.return_value = _mock_resp(MOCK_TOP_MODELS)
            models = client.get_top_models(limit=1)
            assert len(models) == 1
