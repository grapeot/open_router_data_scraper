"""Live integration tests — real OpenRouter API calls.

Gated by ORDS_ENABLE_LIVE_TESTS=1. Do NOT run in CI.
"""
from __future__ import annotations

import os

import pytest

from open_router_data_scraper.client import OpenRouterClient, ActivityRow, ChartPoint

LIVE_ENABLED = os.environ.get("ORDS_ENABLE_LIVE_TESTS", "") == "1"

pytestmark = pytest.mark.skipif(
    not LIVE_ENABLED,
    reason="Set ORDS_ENABLE_LIVE_TESTS=1 to run live tests against real OpenRouter API",
)


@pytest.fixture
def client():
    c = OpenRouterClient()
    yield c
    c.close()


class TestLiveEndpoints:
    def test_get_top_models(self, client):
        models = client.get_top_models(limit=5)
        assert len(models) == 5
        assert all("slug" in m for m in models)

    def test_resolve_canonical_slug(self, client):
        slug = client.resolve_canonical_slug("z-ai/glm-5.2")
        assert "2026" in slug
        assert OpenRouterClient._is_canonical(slug)

    def test_get_model_activity(self, client):
        rows = client.get_model_activity("z-ai/glm-5.2-20260616", variant="standard")
        assert len(rows) > 0
        assert all(isinstance(r, ActivityRow) for r in rows)
        assert rows[0].total_prompt_tokens > 0

    def test_get_rankings_models(self, client):
        rows = client.get_rankings_models(view="day")
        assert len(rows) > 100  # should have 390+ models
        assert all(isinstance(r, ActivityRow) for r in rows)

    def test_get_rankings_chart(self, client):
        points = client.get_rankings_chart()
        assert len(points) > 0
        assert all(isinstance(p, ChartPoint) for p in points)
        assert len(points[-1].values) > 0

    def test_get_tools_chart(self, client):
        points = client.get_tools_chart()
        assert len(points) > 0

    def test_get_task_spend(self, client):
        data = client.get_task_spend()
        assert "spend" in data["data"]

    def test_get_performance(self, client):
        models = client.get_performance()
        assert len(models) > 0

    def test_get_benchmarks(self, client):
        data = client.get_benchmarks()
        assert "aaData" in data["data"]

    def test_get_apps_ranking(self, client):
        data = client.get_apps_ranking()
        assert "day" in data["data"]

    def test_nitro_variant_returns_data(self, client):
        slug = "z-ai/glm-5.2-20260616"
        nitro = client.get_model_activity(slug, variant="nitro")
        # some models don't have a separate nitro variant; API may return standard data
        # just verify the endpoint returns something without error
        assert isinstance(nitro, list)