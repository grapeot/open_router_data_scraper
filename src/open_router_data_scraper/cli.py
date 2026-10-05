"""CLI: `ords` — OpenRouter Data Scraper.

子命令:
  ords discover [--top N]               — 列出 Top-N 模型
  ords fetch <slug> [--variant V]        — 抓取单模型逐日用量并打印
  ords archive [--top N] [--variants V]  — 两层抓取：rankings/models 批量 + top-N 补全，存入 SQLite
  ords snapshot                           — 抓取所有快照端点（task-spend/performance/benchmarks/apps）+ 图表时间序列
  ords digest [--top N] [--output P]      — 从 DB 计算确定性每周 digest packet（md + json）
  ords query [--slug S] [--variant V] [--from D] [--to D]  — 从 DB 查询
  ords models                             — 列出 DB 中已追踪模型
  ords dashboard [--slug S] [--top N]     — 生成 PNG 图表
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from .client import OpenRouterClient
from .digest import build_packet, render_markdown
from .store import Storage


def _fmt_b(n: int | None) -> str:
    if n is None:
        return "-"
    if n >= 1e12:
        return f"{n/1e12:.2f}T"
    if n >= 1e9:
        return f"{n/1e9:.2f}B"
    if n >= 1e6:
        return f"{n/1e6:.1f}M"
    return f"{n:,}"


def cmd_discover(args: argparse.Namespace) -> int:
    client = OpenRouterClient()
    models = client.get_top_models(limit=args.top)
    client.close()
    print(f"# Top-{len(models)} models (by weekly token usage)")
    print(f"{'#':>3}  {'slug':<50} {'canonical_slug':<55} {'name'}")
    for i, m in enumerate(models, 1):
        slug = m.get("slug", "?")
        perma = m.get("permaslug") or m.get("canonical_slug") or slug
        name = m.get("name", "?")[:40]
        print(f"{i:>3}  {slug:<50} {perma:<55} {name}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    client = OpenRouterClient()
    rows = client.get_model_activity(args.slug, variant=args.variant)
    client.close()
    if not rows:
        print(f"# no data for {args.slug} variant={args.variant}", file=sys.stderr)
        return 1
    print(f"# {rows[0].variant_permaslug}  variant={args.variant}  days={len(rows)}  "
          f"range={rows[-1].date[:10]}..{rows[0].date[:10]}")
    print(f"{'date':<12} {'prompt':>10} {'completion':>10} {'reasoning':>10} "
          f"{'cached':>10} {'requests':>12} {'tool_calls':>12}")
    for r in sorted(rows, key=lambda x: x.date):
        print(f"{r.date[:10]:<12} {_fmt_b(r.total_prompt_tokens):>10} "
              f"{_fmt_b(r.total_completion_tokens):>10} "
              f"{_fmt_b(r.total_native_tokens_reasoning):>10} "
              f"{_fmt_b(r.total_native_tokens_cached):>10} "
              f"{r.count:>12,} {r.total_tool_calls:>12,}")
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    """两层抓取：rankings/models 批量层 + top-N model-activity 补全层。"""
    client = OpenRouterClient()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # 层 1: 批量 — rankings/models?view=day（当天实际值，非累积）
    print("# Layer 1: rankings/models?view=day (batch, per-day actual)", file=sys.stderr)
    batch_rows = client.get_rankings_models(view="day")
    with Storage(args.db) as store:
        n_batch = store.upsert_activity(batch_rows)
        models_in_batch = len(set(r.variant_permaslug for r in batch_rows))
        print(f"  batch: {len(batch_rows)} rows, {models_in_batch} models, {n_batch} new",
              file=sys.stderr)

        # 层 2: 补全 — top-N 模型逐个 model-activity（含 reasoning/cached）
        print(f"# Layer 2: model-activity for top-{args.top} (supplement)", file=sys.stderr)
        top_models = client.get_top_models(limit=args.top)
        variants = args.variants.split(",")
        total_supplement = 0
        for m in top_models:
            perma = m.get("permaslug") or m.get("canonical_slug") or m.get("slug", "")
            for v in variants:
                try:
                    rows = client.get_model_activity(perma, variant=v)
                except Exception as e:
                    print(f"# skip {perma} variant={v}: {e}", file=sys.stderr)
                    continue
                n = store.upsert_activity(rows, enrich_existing=True)
                total_supplement += n
                if rows:
                    print(f"  {perma:<55} v={v:<8} days={len(rows):>3} new={n:>3}  "
                          f"range={rows[-1].date[:10]}..{rows[0].date[:10]}")
    client.close()
    print(f"\n# Archived: batch={n_batch} new, supplement={total_supplement} new",
          file=sys.stderr)
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    """抓取所有快照端点 + 图表时间序列，存入 SQLite。"""
    client = OpenRouterClient()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with Storage(args.db) as store:
        # 图表时间序列（rolling, dedup）
        charts = [
            ("rankings_chart", client.get_rankings_chart()),
            ("tools", client.get_tools_chart()),
            ("images", client.get_images_chart()),
            ("modality_text", client.get_modality_chart()),
            ("natural_language_en", client.get_natural_language_chart()),
            ("programming_language_python", client.get_programming_language_chart()),
        ]
        for name, points in charts:
            n = store.upsert_chart(name, points)
            print(f"  chart {name:<30} points={len(points):>4} new={n}", file=sys.stderr)

        # 快照
        ts = client.get_task_spend()
        n = store.snapshot_task_spend(now, ts)
        print(f"  snapshot task_spend                  new={n}", file=sys.stderr)

        perf = client.get_performance()
        n = store.snapshot_performance(now, perf)
        print(f"  snapshot performance  ({len(perf)} models) new={n}", file=sys.stderr)

        bench = client.get_benchmarks()
        n = store.snapshot_benchmarks(now, bench)
        print(f"  snapshot benchmarks                  new={n}", file=sys.stderr)

        apps = client.get_apps_ranking()
        n = store.snapshot_apps(now, apps)
        print(f"  snapshot apps                        new={n}", file=sys.stderr)
    client.close()
    print(f"\n# Snapshots stored at {now}", file=sys.stderr)
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    """Compute the deterministic weekly digest packet from SQLite."""
    import json
    from pathlib import Path

    with Storage(args.db) as store:
        try:
            packet = build_packet(store.conn, top_n=args.top)
        except ValueError as e:
            print(f"# {e}", file=sys.stderr)
            return 1
    markdown = render_markdown(packet)

    out = args.output or f"data/digest_{packet['quality']['newest_date']}.md"
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")
    json_path = out_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(markdown)
    print(f"# wrote {out_path} and {json_path}", file=sys.stderr)
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    with Storage(args.db) as store:
        rows = store.query_activity(
            slug=args.slug, variant=args.variant,
            date_from=args.from_, date_to=args.to,
        )
    if not rows:
        print("# no data found", file=sys.stderr)
        return 1
    print(f"# {len(rows)} rows  variant={args.variant}")
    print(f"{'date':<12} {'slug':<50} {'prompt':>10} {'completion':>10} "
          f"{'reasoning':>10} {'requests':>12}")
    for r in rows:
        print(f"{r['date'][:10]:<12} {r['variant_permaslug']:<50} "
              f"{_fmt_b(r['total_prompt_tokens']):>10} "
              f"{_fmt_b(r['total_completion_tokens']):>10} "
              f"{_fmt_b(r['total_native_tokens_reasoning']):>10} "
              f"{r['count']:>12,}")
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    with Storage(args.db) as store:
        models = store.tracked_models()
    if not models:
        print("# no models in DB yet, run `ords archive` first", file=sys.stderr)
        return 1
    print(f"# {len(models)} models in DB:")
    for m in models:
        lo, hi = _date_range_safe(args.db, m)
        print(f"  {m:<55} {lo[:10] if lo else '?'}..{hi[:10] if hi else '?'}")
    return 0


def _date_range_safe(db_path, slug):
    with Storage(db_path) as s:
        return s.date_range(slug)


def cmd_dashboard(args: argparse.Namespace) -> int:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print("# matplotlib not installed. Install with: uv pip install -e '.[viz]'",
              file=sys.stderr)
        return 1

    from datetime import datetime as dt

    with Storage(args.db) as store:
        if args.slug:
            slugs = [args.slug]
        else:
            all_models = store.tracked_models()
            slugs = all_models[:args.top] if all_models else []
        if not slugs:
            print("# no data in DB. Run `ords archive` first.", file=sys.stderr)
            return 1

        fig, axes = plt.subplots(2, 1, figsize=(14, 9), sharex=True)
        for slug in slugs:
            rows = store.query_activity(slug=slug, variant="standard")
            if not rows:
                continue
            dates = [dt.strptime(r["date"][:10], "%Y-%m-%d") for r in rows]
            prompt = [r["total_prompt_tokens"] / 1e9 for r in rows]
            completion = [r["total_completion_tokens"] / 1e9 for r in rows]
            label = slug.split("/")[-1][:30]
            axes[0].plot(dates, prompt, label=label, linewidth=1.5)
            axes[1].plot(dates, completion, label=label, linewidth=1.5)

        axes[0].set_ylabel("Prompt tokens (B)")
        axes[0].set_title("OpenRouter Model Activity — Prompt Tokens")
        axes[0].legend(fontsize=7, loc="upper left")
        axes[0].grid(True, alpha=0.3)
        axes[1].set_ylabel("Completion tokens (B)")
        axes[1].set_title("Completion Tokens")
        axes[1].legend(fontsize=7, loc="upper left")
        axes[1].grid(True, alpha=0.3)
        axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        axes[1].xaxis.set_major_locator(mdates.AutoDateLocator())
        plt.tight_layout()
        out = args.output or "data/dashboard.png"
        from pathlib import Path
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(out, dpi=150)
        print(f"# saved to {out}")
        plt.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ords", description="OpenRouter data scraper")
    sub = p.add_subparsers(dest="command", required=True)

    def _add_db(sp):
        sp.add_argument("--db", default=None, help="SQLite DB path (default: data/ords.db)")

    sp = sub.add_parser("discover", help="List Top-N models")
    _add_db(sp)
    sp.add_argument("--top", type=int, default=20)
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser("fetch", help="Fetch and print single model activity")
    sp.add_argument("slug", help="Model slug (short or canonical)")
    sp.add_argument("--variant", default="standard",
                    choices=["standard", "nitro", "floor", "exacto"])
    sp.set_defaults(func=cmd_fetch)

    sp = sub.add_parser("archive", help="Two-layer scrape: batch + supplement, store to SQLite")
    _add_db(sp)
    sp.add_argument("--top", type=int, default=20, help="Top-N models for supplement layer")
    sp.add_argument("--variants", default="standard", help="Comma-separated variants")
    sp.set_defaults(func=cmd_archive)

    sp = sub.add_parser("snapshot", help="Scrape all snapshot + chart endpoints to SQLite")
    _add_db(sp)
    sp.set_defaults(func=cmd_snapshot)

    sp = sub.add_parser("digest", help="Compute the deterministic weekly digest packet")
    _add_db(sp)
    sp.add_argument("--top", type=int, default=20)
    sp.add_argument("--output", default=None, help="Markdown output path (default: data/digest_<date>.md)")
    sp.set_defaults(func=cmd_digest)

    sp = sub.add_parser("query", help="Query stored data from SQLite")
    _add_db(sp)
    sp.add_argument("--slug", default=None)
    sp.add_argument("--variant", default="standard")
    sp.add_argument("--from", dest="from_", default=None, help="YYYY-MM-DD")
    sp.add_argument("--to", dest="to", default=None, help="YYYY-MM-DD")
    sp.set_defaults(func=cmd_query)

    sp = sub.add_parser("models", help="List tracked models in DB")
    _add_db(sp)
    sp.set_defaults(func=cmd_models)

    sp = sub.add_parser("dashboard", help="Generate chart from DB data")
    _add_db(sp)
    sp.add_argument("--slug", default=None)
    sp.add_argument("--top", type=int, default=10)
    sp.add_argument("--output", default=None)
    sp.set_defaults(func=cmd_dashboard)

    return p


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
