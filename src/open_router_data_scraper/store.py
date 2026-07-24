"""SQLite storage layer — rolling data dedup + snapshot data with timestamp.

表:
  - model_activity:        逐日活动数据，PK=(variant_permaslug, date, variant)
  - chart_series:          时间序列图表，PK=(endpoint, date, slug)
  - snapshot_task_spend:   task-spend 快照，PK=(snapshot_date, category_key)
  - snapshot_performance:  performance 快照，PK=(snapshot_date, slug)
  - snapshot_benchmarks:   benchmarks 快照，PK=(snapshot_date, uid, category)
  - snapshot_apps:          apps 排名快照，PK=(snapshot_date, window, app_id)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .client import ActivityRow, ChartPoint

DEFAULT_DB = Path(__file__).parent.parent.parent / "data" / "ords.db"

SCHEMA = """
-- Rolling: 活动数据（rankings/models 批量层 + model-activity 补全层合并）
CREATE TABLE IF NOT EXISTS model_activity (
    date TEXT NOT NULL,
    variant_permaslug TEXT NOT NULL,
    variant TEXT NOT NULL,
    total_prompt_tokens INTEGER,
    total_completion_tokens INTEGER,
    total_native_tokens_reasoning INTEGER,
    total_native_tokens_cached INTEGER,
    count INTEGER,
    total_tool_calls INTEGER,
    requests_with_tool_call_errors INTEGER,
    num_media_prompt INTEGER,
    num_media_completion INTEGER,
    image_output_requests INTEGER,
    num_video_prompt INTEGER,
    video_output_seconds INTEGER,
    rerank_documents INTEGER,
    stt_transcript_characters INTEGER,
    num_audio_prompt INTEGER,
    PRIMARY KEY (variant_permaslug, date, variant)
);

-- Provenance for distinguishing batch structural zeros from supplemented telemetry.
CREATE TABLE IF NOT EXISTS activity_coverage (
    date TEXT NOT NULL,
    variant_permaslug TEXT NOT NULL,
    variant TEXT NOT NULL,
    batch_seen INTEGER NOT NULL DEFAULT 0,
    supplement_seen INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (variant_permaslug, date, variant)
);

-- Rolling: 图表时间序列
CREATE TABLE IF NOT EXISTS chart_series (
    endpoint TEXT NOT NULL,      -- 'rankings_chart', 'tools', 'images', etc.
    date TEXT NOT NULL,          -- 'YYYY-MM-DD'
    slug TEXT NOT NULL,
    tokens INTEGER,
    PRIMARY KEY (endpoint, date, slug)
);

-- Snapshot: task-spend
CREATE TABLE IF NOT EXISTS snapshot_task_spend (
    snapshot_date TEXT NOT NULL,
    category_key TEXT NOT NULL,
    category_label TEXT,
    spend_share REAL,
    PRIMARY KEY (snapshot_date, category_key)
);

-- Snapshot: performance
CREATE TABLE IF NOT EXISTS snapshot_performance (
    snapshot_date TEXT NOT NULL,
    slug TEXT NOT NULL,
    name TEXT,
    author TEXT,
    request_count INTEGER,
    p50_latency REAL,
    p50_throughput REAL,
    data_json TEXT,               -- 完整 JSON dump 以防字段扩展
    PRIMARY KEY (snapshot_date, slug)
);

-- Snapshot: benchmarks
CREATE TABLE IF NOT EXISTS snapshot_benchmarks (
    snapshot_date TEXT NOT NULL,
    category TEXT NOT NULL,       -- 'intelligence', 'coding', etc.
    uid TEXT NOT NULL,
    permaslug TEXT,
    name TEXT,
    score REAL,
    PRIMARY KEY (snapshot_date, category, uid)
);

-- Snapshot: apps ranking
CREATE TABLE IF NOT EXISTS snapshot_apps (
    snapshot_date TEXT NOT NULL,
    window TEXT NOT NULL,         -- 'day', 'week', 'month'
    app_id INTEGER NOT NULL,
    rank INTEGER,
    total_tokens TEXT,
    total_requests INTEGER,
    app_json TEXT,                 -- 完整 app 对象
    PRIMARY KEY (snapshot_date, window, app_id)
);

CREATE INDEX IF NOT EXISTS idx_activity_date ON model_activity(date);
CREATE INDEX IF NOT EXISTS idx_activity_slug ON model_activity(variant_permaslug);
CREATE INDEX IF NOT EXISTS idx_chart_endpoint ON chart_series(endpoint);
"""


class Storage:
    def __init__(self, db_path: Path | str | None = None) -> None:
        path = Path(db_path) if db_path else DEFAULT_DB
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ── Rolling: model_activity ────────────────────────────────────

    def upsert_activity(
        self, rows: list[ActivityRow], *, enrich_existing: bool = False
    ) -> int:
        if not rows:
            return 0
        cur = self.conn.cursor()
        inserted = 0
        for r in rows:
            values = (
                r.date, r.variant_permaslug, r.variant,
                r.total_prompt_tokens, r.total_completion_tokens,
                r.total_native_tokens_reasoning, r.total_native_tokens_cached,
                r.count, r.total_tool_calls, r.requests_with_tool_call_errors,
                r.num_media_prompt, r.num_media_completion,
                r.image_output_requests, r.num_video_prompt,
                r.video_output_seconds, r.rerank_documents,
                r.stt_transcript_characters, r.num_audio_prompt,
            )
            cur.execute(
                "INSERT OR IGNORE INTO model_activity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                values,
            )
            inserted += cur.rowcount
            if enrich_existing and cur.rowcount == 0:
                cur.execute(
                    """UPDATE model_activity SET
                        total_native_tokens_reasoning=?, total_native_tokens_cached=?,
                        total_tool_calls=?, requests_with_tool_call_errors=?
                    WHERE date=? AND variant_permaslug=? AND variant=?""",
                    (values[5], values[6], values[8], values[9], *values[:3]),
                )
            cur.execute(
                """INSERT INTO activity_coverage VALUES (?,?,?,?,?)
                ON CONFLICT(variant_permaslug, date, variant) DO UPDATE SET
                    batch_seen=MAX(batch_seen, excluded.batch_seen),
                    supplement_seen=MAX(supplement_seen, excluded.supplement_seen)""",
                (
                    r.date, r.variant_permaslug, r.variant,
                    int(not enrich_existing), int(enrich_existing),
                ),
            )
        self.conn.commit()
        return inserted

    def query_activity(
        self, slug: str | None = None, variant: str = "standard",
        date_from: str | None = None, date_to: str | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM model_activity WHERE variant = ?"
        params: list[Any] = [variant]
        if slug:
            sql += " AND variant_permaslug LIKE ?"
            params.append(f"%{slug}%")
        if date_from:
            sql += " AND date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND date <= ?"
            params.append(date_to)
        sql += " ORDER BY date ASC"
        cur = self.conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def date_range(self, slug: str | None = None) -> tuple[str | None, str | None]:
        sql = "SELECT MIN(date), MAX(date) FROM model_activity"
        params: list[Any] = []
        if slug:
            sql += " WHERE variant_permaslug LIKE ?"
            params.append(f"%{slug}%")
        row = self.conn.execute(sql, params).fetchone()
        return row[0], row[1]

    def tracked_models(self) -> list[str]:
        cur = self.conn.execute(
            "SELECT DISTINCT variant_permaslug FROM model_activity ORDER BY variant_permaslug"
        )
        return [r[0] for r in cur.fetchall()]

    # ── Rolling: chart_series ──────────────────────────────────────

    def upsert_chart(self, endpoint: str, points: list[ChartPoint]) -> int:
        if not points:
            return 0
        cur = self.conn.cursor()
        inserted = 0
        for pt in points:
            slugs = list(pt.values)
            if slugs:
                placeholders = ",".join("?" for _ in slugs)
                cur.execute(
                    f"""DELETE FROM chart_series
                    WHERE endpoint=? AND date=? AND slug NOT IN ({placeholders})""",
                    [endpoint, pt.date, *slugs],
                )
            else:
                cur.execute(
                    "DELETE FROM chart_series WHERE endpoint=? AND date=?",
                    (endpoint, pt.date),
                )
            for slug, tokens in pt.values.items():
                cur.execute(
                    "INSERT OR IGNORE INTO chart_series VALUES (?,?,?,?)",
                    (endpoint, pt.date, slug, tokens),
                )
                inserted += cur.rowcount
                if cur.rowcount == 0:
                    cur.execute(
                        "UPDATE chart_series SET tokens=? WHERE endpoint=? AND date=? AND slug=?",
                        (tokens, endpoint, pt.date, slug),
                    )
        self.conn.commit()
        return inserted

    def query_chart(self, endpoint: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT date, slug, tokens FROM chart_series WHERE endpoint = ? ORDER BY date ASC",
            (endpoint,),
        )
        return [dict(zip(["date", "slug", "tokens"], r)) for r in cur.fetchall()]

    # ── Snapshot writers ───────────────────────────────────────────

    def snapshot_task_spend(self, snapshot_date: str, data: dict) -> int:
        # data is the full API response: {"data": {"spend": {"macroCategories": [...]}}}
        spend = data.get("data", {}).get("spend", {})
        if not spend:
            spend = data.get("spend", {})  # fallback if already unwrapped
        cats = spend.get("macroCategories", [])
        cur = self.conn.cursor()
        n = 0
        for c in cats:
            cur.execute(
                "INSERT OR IGNORE INTO snapshot_task_spend VALUES (?,?,?,?)",
                (snapshot_date, c.get("key", ""), c.get("label", ""), c.get("spendShare", 0)),
            )
            n += cur.rowcount
        # also store tokens section if present
        self.conn.commit()
        return n

    def snapshot_performance(self, snapshot_date: str, models: list[dict]) -> int:
        cur = self.conn.cursor()
        n = 0
        for m in models:
            cur.execute(
                "INSERT OR IGNORE INTO snapshot_performance VALUES (?,?,?,?,?,?,?,?)",
                (
                    snapshot_date, m.get("slug") or m.get("id", ""),
                    m.get("name", ""), m.get("author", ""),
                    m.get("request_count"), m.get("p50_latency"),
                    m.get("p50_throughput"), json.dumps(m),
                ),
            )
            n += cur.rowcount
        self.conn.commit()
        return n

    def snapshot_benchmarks(self, snapshot_date: str, data: dict) -> int:
        cur = self.conn.cursor()
        n = 0
        inner = data.get("data", data)
        aa = inner.get("aaData") if isinstance(inner, dict) else None
        if not isinstance(aa, dict) or not aa:
            raise TypeError("benchmark response must contain a non-empty aaData object")
        categories = 0
        for category, items in aa.items():
            if category == "percentilesBySlug":
                continue
            if not isinstance(items, list):
                raise TypeError(f"benchmark category {category!r} must be a list")
            categories += 1
            for item in items:
                if not isinstance(item, dict):
                    raise TypeError(f"benchmark item in {category!r} must be an object")
                if not item.get("uid"):
                    raise ValueError(f"benchmark item in {category!r} must have a uid")
                cur.execute(
                    "INSERT OR IGNORE INTO snapshot_benchmarks VALUES (?,?,?,?,?,?)",
                    (
                        snapshot_date, category,
                        item.get("uid", ""), item.get("permaslug"),
                        item.get("aa_name") or item.get("name", ""),
                        item.get("score"),
                    ),
                )
                n += cur.rowcount
        if categories == 0:
            raise ValueError("benchmark response contains no benchmark categories")
        self.conn.commit()
        return n

    def snapshot_apps(self, snapshot_date: str, data: dict) -> int:
        # data is the full API response: {"data": {"day": [...], "week": [...], ...}}
        inner = data.get("data", data)
        cur = self.conn.cursor()
        n = 0
        for window in ("day", "week", "month"):
            entries = inner.get(window, [])
            for e in entries:
                app = e.get("app", {})
                cur.execute(
                    "INSERT OR IGNORE INTO snapshot_apps VALUES (?,?,?,?,?,?,?)",
                    (
                        snapshot_date, window,
                        e.get("app_id") or app.get("id"),
                        e.get("rank"),
                        e.get("total_tokens"),
                        e.get("total_requests"),
                        json.dumps(e),
                    ),
                )
                n += cur.rowcount
        self.conn.commit()
        return n

    # ── Stats ──────────────────────────────────────────────────────

    def table_count(self, table: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
