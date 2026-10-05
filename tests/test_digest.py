"""Unit tests for the deterministic weekly digest packet.

Each test locks one contract from ``schema/model_activity.schema.json`` with a
fixture that fails under the corresponding bug, not a fixture that happens to
pass.
"""
from __future__ import annotations

import pytest

from open_router_data_scraper.client import ActivityRow
from open_router_data_scraper.digest import build_packet, render_markdown
from open_router_data_scraper.store import Storage


def _act(
    date, slug, variant="standard", prompt=100_000, completion=5_000,
    cached=80_000, reasoning=1_000, count=1_000,
):
    return ActivityRow(
        date=date, model_permaslug=slug, variant=variant,
        total_prompt_tokens=prompt, total_completion_tokens=completion,
        total_native_tokens_reasoning=reasoning, total_native_tokens_cached=cached,
        count=count, total_tool_calls=0, requests_with_tool_call_errors=0,
        num_media_prompt=0, num_media_completion=0, image_output_requests=0,
        num_video_prompt=0, video_output_seconds=0, rerank_documents=0,
        stt_transcript_characters=0, num_audio_prompt=0, variant_permaslug=slug,
    )


def _batch_pad(store, date, rows=310):
    """Batch-only rows (supplement_seen=0), no tokens, to clear the market threshold."""
    store.upsert_activity([
        _act(date, f"pad/seed-{i:04d}-20260101", prompt=0, completion=0,
             cached=0, reasoning=0, count=1)
        for i in range(rows)
    ])


def _cover(store, date, rows):
    """Supplement-covered rows for a day (marks supplement_seen=1)."""
    store.upsert_activity(rows, enrich_existing=True)


def _day(store, date, slugs, prompt=100_000, completion=5_000, cached=80_000,
         reasoning=1_000, count=1_000):
    """Supplement-cover `slugs` plus two stable fillers so the day has >=3 covered rows."""
    covered = list(slugs) + ["org/fill-a-20260101", "org/fill-b-20260101"]
    _cover(store, date, [
        _act(date, s, prompt=prompt, completion=completion, cached=cached,
             reasoning=reasoning, count=count) for s in covered
    ])


class TestQualityGate:
    def test_empty_db_raises(self, tmp_path):
        with Storage(db_path=tmp_path / "t.db") as store:
            with pytest.raises(ValueError, match="empty"):
                build_packet(store.conn)

    def test_flags_partial_newest(self, tmp_path):
        with Storage(db_path=tmp_path / "t.db") as store:
            _batch_pad(store, "2026-09-24")
            store.upsert_activity([_act("2026-09-25", "org/panel-20260101")])
            p = build_packet(store.conn, generated_at="2026-09-26T00:00:00Z")
            assert p["quality"]["newest_date"] == "2026-09-25"
            assert p["quality"]["newest_is_market_day"] is False
            assert any("partial" in w for w in p["quality"]["warnings"])
            # partial newest must not be in the panel window
            assert "2026-09-25" not in p["quality"]["panel_days_used"]

    def test_newest_market_day_stays_in_panel(self, tmp_path):
        with Storage(db_path=tmp_path / "t.db") as store:
            days = [f"2026-09-{d:02d}" for d in range(18, 24)]  # six market days
            for date in days:
                _day(store, date, ["org/steady-20260101"])
                _batch_pad(store, date)
            p = build_packet(store.conn, generated_at="2026-09-24T00:00:00Z")
            assert p["quality"]["newest_is_market_day"] is True
            assert days[-1] in p["quality"]["panel_days_used"]
            assert p["panel"]["available"] is True
            assert days[-1] in (p["panel"]["prev_days"] + p["panel"]["last_days"])

    def test_detects_contaminated_day(self, tmp_path):
        with Storage(db_path=tmp_path / "t.db") as store:
            _day(store, "2026-09-10", ["org/a-20260101"], cached=90_000, prompt=100_000)
            # cached > prompt = mixed vintage
            _day(store, "2026-09-11", ["org/a-20260101"], cached=250_000, prompt=100_000)
            _batch_pad(store, "2026-09-12")
            p = build_packet(store.conn, generated_at="2026-09-13T00:00:00Z")
            assert "2026-09-11" in p["quality"]["contaminated_days"]
            assert "2026-09-10" not in p["quality"]["contaminated_days"]
            assert "2026-09-11" not in p["quality"]["panel_days_used"]


class TestMarket:
    def test_exact_totals_ignore_partial_days(self, tmp_path):
        with Storage(db_path=tmp_path / "t.db") as store:
            # two clean market days with exact, known tokens
            for date, deep, xai in (("2026-09-24", 100_000, 50_000),
                                    ("2026-10-01", 200_000, 50_000)):
                _cover(store, date, [
                    _act(date, "deepseek/x-20260101", prompt=deep, completion=0,
                         cached=0, count=10),
                    _act(date, "x-ai/y-20260101", prompt=xai, completion=0,
                         cached=0, count=10),
                ])
                _batch_pad(store, date)
            # a high-token but partial day (<300 rows) that must never be summed in
            _cover(store, "2026-09-30", [
                _act("2026-09-30", "bigco/huge-20260101", prompt=999_000_000, completion=0,
                     cached=0, count=10),
            ])
            p = build_packet(store.conn, generated_at="2026-10-02T00:00:00Z")
            m = p["market"]
            assert m["prev_date"] == "2026-09-24"
            assert m["cur_date"] == "2026-10-01"
            assert m["tokens_prev"] == 150_000
            assert m["tokens_cur"] == 250_000

    def test_contaminated_market_day_excluded(self, tmp_path):
        with Storage(db_path=tmp_path / "t.db") as store:
            _cover(store, "2026-09-24", [
                _act("2026-09-24", "deepseek/x-20260101", prompt=100_000, completion=0,
                     cached=0, count=10)])
            _batch_pad(store, "2026-09-24")
            _cover(store, "2026-10-01", [
                _act("2026-10-01", "deepseek/x-20260101", prompt=200_000, completion=0,
                     cached=300_000, count=10)])  # cached > prompt = contaminated
            _batch_pad(store, "2026-10-01")
            p = build_packet(store.conn, generated_at="2026-10-02T00:00:00Z")
            assert "2026-10-01" in p["quality"]["contaminated_days"]
            assert p["market"].get("available") is False  # only one clean market day left


class TestPanel:
    def test_cache_rate_uses_cached_over_prompt(self, tmp_path):
        with Storage(db_path=tmp_path / "t.db") as store:
            for date in [f"2026-09-{d:02d}" for d in range(15, 29)]:
                _day(store, date, ["org/m-20260101"], prompt=100_000, completion=5_000,
                     cached=80_000)
                _batch_pad(store, date)
            p = build_packet(store.conn, generated_at="2026-09-29T00:00:00Z")
            m = next(x for x in p["panel"]["models"] if x["slug"] == "org/m-20260101")
            # 80_000 / 100_000, not 80_000 / 105_000
            assert abs(m["cache_rate_last"] - 0.8) < 1e-9

    def test_reasoning_rate_uses_same_window_completion(self, tmp_path):
        with Storage(db_path=tmp_path / "t.db") as store:
            days = [f"2026-09-{d:02d}" for d in range(15, 29)]
            half = len(days) // 2
            for i, date in enumerate(days):
                comp = 1_000 if i < half else 4_000  # prev vs last completion differ
                _day(store, date, ["org/m-20260101"], prompt=10_000, completion=comp,
                     cached=5_000, reasoning=2_000)
                _batch_pad(store, date)  # every day (incl. newest) is a market day
            p = build_packet(store.conn, generated_at="2026-09-29T00:00:00Z")
            m = next(x for x in p["panel"]["models"] if x["slug"] == "org/m-20260101")
            # reasoning(2000) / completion(4000) in the LAST window = 0.5
            assert abs(m["reasoning_rate_last"] - 0.5) < 1e-9

    def test_cohort_requires_all_days(self, tmp_path):
        with Storage(db_path=tmp_path / "t.db") as store:
            for date in [f"2026-09-{d:02d}" for d in range(15, 29)]:
                _day(store, date, ["org/steady-20260101"])
                _batch_pad(store, date)
            _cover(store, "2026-09-27", [_act("2026-09-27", "org/partial-20260101")])
            p = build_packet(store.conn, generated_at="2026-09-29T00:00:00Z")
            slugs = {x["slug"] for x in p["panel"]["models"]}
            assert "org/steady-20260101" in slugs
            assert "org/partial-20260101" not in slugs

    def test_batch_only_rows_not_in_panel_rates(self, tmp_path):
        with Storage(db_path=tmp_path / "t.db") as store:
            # a slug that only ever appears as batch rows (supplement_seen=0)
            for date in [f"2026-09-{d:02d}" for d in range(15, 29)]:
                _day(store, date, ["org/covered-20260101"])
                _batch_pad(store, date, rows=1)
            store.upsert_activity([
                _act(f"2026-09-{d:02d}", "org/batchonly-20260101", cached=0, reasoning=0)
                for d in range(15, 29)
            ])
            p = build_packet(store.conn, generated_at="2026-09-29T00:00:00Z")
            slugs = {x["slug"] for x in p["panel"]["models"]}
            assert "org/batchonly-20260101" not in slugs


class TestAnomalies:
    def test_structural_zero_not_flagged(self, tmp_path):
        with Storage(db_path=tmp_path / "t.db") as store:
            store.upsert_activity([_act("2026-09-30", "org/batchonly-20260101",
                                        cached=0, reasoning=0)])
            _day(store, "2026-09-30", ["org/covered-20260101"])
            p = build_packet(store.conn, generated_at="2026-09-30T00:00:00Z")
            assert all(a["slug"] != "org/batchonly-20260101" for a in p["anomalies"])

    def test_zero_cache_covered_is_flagged(self, tmp_path):
        with Storage(db_path=tmp_path / "t.db") as store:
            _day(store, "2026-09-30", ["org/zero-20260101"], cached=0)
            _cover(store, "2026-09-30", [_act("2026-09-30", "org/other-20260101")])
            _batch_pad(store, "2026-09-30")
            p = build_packet(store.conn, generated_at="2026-09-30T00:00:00Z")
            hit = [a for a in p["anomalies"] if a["slug"] == "org/zero-20260101"]
            assert hit and "cache_rate_below_5pct" in hit[0]["reasons"]

    def test_anomaly_date_is_clean_covered_day(self, tmp_path):
        with Storage(db_path=tmp_path / "t.db") as store:
            _day(store, "2026-09-30", ["org/zero-20260101"], cached=0)
            _cover(store, "2026-09-30", [_act("2026-09-30", "org/other-20260101")])
            _batch_pad(store, "2026-09-30")
            # a partial newest day after it must not be used for anomalies
            store.upsert_activity([_act("2026-10-01", "org/zero-20260101", cached=0)])
            p = build_packet(store.conn, generated_at="2026-10-02T00:00:00Z")
            for a in p["anomalies"]:
                assert a["date"] == "2026-09-30"


class TestRender:
    def test_render_has_all_sections(self, tmp_path):
        with Storage(db_path=tmp_path / "t.db") as store:
            _day(store, "2026-09-30", ["org/a-20260101"])
            _batch_pad(store, "2026-09-30")
            p = build_packet(store.conn, generated_at="2026-09-30T00:00:00Z")
            md = render_markdown(p)
            for section in ("A. Data quality gate", "B. Whole-market",
                            "C. Top-N panel", "D. Newcomers", "E. External enrichment"):
                assert section in md
