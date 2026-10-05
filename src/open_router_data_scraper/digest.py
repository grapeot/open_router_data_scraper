"""Deterministic weekly digest packet.

Turns the mixed-provenance ``model_activity`` archive into one auditable
packet that follows the interpretation contract in
``schema/model_activity.schema.json``. This module is the "compute" side of a
compute/talk split: it produces fixed, reproducible numbers. A downstream agent
reads the packet and writes prose; it must not re-derive metrics on its own.

Correctness rules encoded here (each came from a real mistake):

* Whole-market totals and provider shares use only clean days with
  ``COUNT(*) >= 300`` and no mixed-vintage contamination
  (``market-accounting`` / ``market-snapshot-days``). Smaller days are a
  changing top-N panel and must never be summed as market traffic.
* Cache rate is ``cached / prompt``, never ``cached / (prompt + completion)``.
* A covered day whose aggregate cached tokens exceed aggregate prompt tokens is
  physically impossible and marks a mixed-vintage row set; that day is dropped
  from every trend and from the market series (``contaminated_days``).
* The newest day is excluded only when it is not a clean whole-market day
  (``partial-current-day``).
* reasoning/cached/tool fields are analyzed only where
  ``activity_coverage.supplement_seen = 1``; a batch-only zero is a structural
  zero and must not enter a rate or trigger an anomaly.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

WHOLE_MARKET_MIN_ROWS = 300
CONTAM_CACHE_RATIO = 1.0
CACHE_HIGH = 0.98
CACHE_LOW = 0.05
PANEL_DAYS = 14
NEWCOMER_LOOKBACK_DAYS = 14

DEFAULT_TOP_N = 20
MARKET_PROVIDER_CAP = 12


def _fmt_b(n: float | int | None) -> str:
    if n is None:
        return "-"
    n = float(n)
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return f"{n:,.0f}"


def _fmt_req(n: float | int | None) -> str:
    if n is None:
        return "-"
    n = float(n)
    for unit, div in (("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return f"{n:,.0f}"


def _fmt_pct(x: float | None, digits: int = 1) -> str:
    return "-" if x is None else f"{x * 100:.{digits}f}%"


def _fmt_delta(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:+.0f}%"


# ── low-level readers ──────────────────────────────────────────────


def _date_rows(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    cur = conn.execute(
        "SELECT substr(date, 1, 10) AS d, COUNT(*) FROM model_activity GROUP BY d ORDER BY d"
    )
    return [(d, n) for d, n in cur.fetchall()]


def _covered_days(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute(
        """SELECT substr(m.date, 1, 10) AS d, COUNT(*) FROM model_activity m
        JOIN activity_coverage ac
          ON m.date = ac.date AND m.variant_permaslug = ac.variant_permaslug
         AND m.variant = ac.variant
        WHERE ac.supplement_seen = 1
        GROUP BY d ORDER BY d"""
    )
    return [d for d, n in cur.fetchall() if n >= 3]


def _contaminated_days(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute(
        """SELECT substr(m.date, 1, 10) AS d,
                  SUM(m.total_prompt_tokens) AS p, SUM(m.total_native_tokens_cached) AS c
        FROM model_activity m
        JOIN activity_coverage ac
          ON m.date = ac.date AND m.variant_permaslug = ac.variant_permaslug
         AND m.variant = ac.variant
        WHERE m.variant = 'standard' AND ac.supplement_seen = 1
        GROUP BY d HAVING p > 0 AND c > %s * p ORDER BY d""" % CONTAM_CACHE_RATIO
    )
    return [d for d, _, _ in cur.fetchall()]


def _slug_rows_in(
    conn: sqlite3.Connection, days: list[str]
) -> dict[str, dict[str, dict]]:
    """Supplement-covered standard rows per slug per day (structural zeros excluded)."""
    if not days:
        return {}
    placeholders = ",".join("?" for _ in days)
    cur = conn.execute(
        f"""SELECT substr(m.date, 1, 10) AS d, m.variant_permaslug,
                   m.total_prompt_tokens, m.total_completion_tokens,
                   m.total_native_tokens_cached, m.total_native_tokens_reasoning, m.count
        FROM model_activity m
        JOIN activity_coverage ac
          ON m.date = ac.date AND m.variant_permaslug = ac.variant_permaslug
         AND m.variant = ac.variant
        WHERE m.variant = 'standard' AND ac.supplement_seen = 1
          AND substr(m.date, 1, 10) IN ({placeholders})""",
        days,
    )
    out: dict[str, dict[str, dict]] = {}
    for d, slug, p, comp, cached, reason, n in cur.fetchall():
        out.setdefault(slug, {})[d] = {
            "prompt": p or 0, "completion": comp or 0,
            "cached": cached or 0, "reasoning": reason or 0, "requests": n or 0,
        }
    return out


def _avg(rows: list[dict], field: str) -> float | None:
    vals = [r[field] for r in rows if r.get(field) is not None]
    return sum(vals) / len(vals) if vals else None


# ── packet assembly ─────────────────────────────────────────────────


def build_packet(
    conn: sqlite3.Connection, top_n: int = DEFAULT_TOP_N,
    generated_at: str | None = None,
) -> dict[str, Any]:
    rows_by_date = _date_rows(conn)
    if not rows_by_date:
        raise ValueError("model_activity is empty; run `ords archive` first")

    all_dates = [d for d, _ in rows_by_date]
    newest_overall = all_dates[-1]
    contaminated = set(_contaminated_days(conn))
    raw_market = [d for d, n in rows_by_date if n >= WHOLE_MARKET_MIN_ROWS]
    newest_is_market_day = newest_overall in raw_market
    newest_valid = newest_is_market_day and newest_overall not in contaminated
    market_days = [d for d in raw_market if d not in contaminated]
    covered = _covered_days(conn)
    covered_clean = [
        d for d in covered
        if d not in contaminated and (d != newest_overall or newest_valid)
    ]

    warnings: list[str] = []
    if contaminated:
        warnings.append(
            "aggregate cached tokens exceed prompt on "
            + ", ".join(sorted(contaminated))
            + "; those days are dropped from all trends and the market series"
        )
    if not newest_is_market_day:
        warnings.append(
            f"newest day {newest_overall} is a partial top-N panel, not a whole-market "
            f"day; excluded from trends"
        )
    elif newest_overall in contaminated:
        warnings.append(
            f"newest whole-market day {newest_overall} is contaminated and excluded"
        )
    if not covered_clean:
        warnings.append("no clean supplement-covered days; panel and anomalies are empty")

    quality = {
        "newest_date": newest_overall,
        "newest_is_market_day": newest_is_market_day,
        "market_days": market_days[-6:],
        "contaminated_days": sorted(contaminated),
        "panel_days_used": covered_clean[-PANEL_DAYS:],
        "clean_covered_day_count": len(covered_clean),
        "warnings": warnings,
    }

    packet: dict[str, Any] = {
        "generated_at": generated_at
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "top_n": top_n,
        "quality": quality,
        "market": _build_market(conn, market_days, top_n),
        "panel": _build_panel(conn, covered_clean, top_n),
    }
    packet["newcomers"] = _build_newcomers(conn, covered_clean, top_n)
    packet["anomalies"] = _build_anomalies(conn, covered_clean)
    packet["enrichment_candidates"] = _build_candidates(
        packet["newcomers"], packet["panel"], packet["anomalies"]
    )
    return packet


def _build_market(
    conn: sqlite3.Connection, market_days: list[str], top_n: int
) -> dict[str, Any]:
    if len(market_days) < 2:
        return {"available": False, "reason": "fewer than two clean whole-market days"}
    prev, cur = market_days[-2], market_days[-1]

    def totals(day: str) -> tuple[int, int, dict[str, list[int]]]:
        rows = conn.execute(
            """SELECT variant_permaslug, total_prompt_tokens + total_completion_tokens,
                      count
            FROM model_activity WHERE substr(date, 1, 10) = ?""",
            (day,),
        ).fetchall()
        tot_t = tot_r = 0
        by_ns: dict[str, list[int]] = {}
        for slug, t, n in rows:
            t = t or 0
            n = n or 0
            tot_t += t
            tot_r += n
            ns = (slug or "?").split("/")[0]
            acc = by_ns.setdefault(ns, [0, 0])
            acc[0] += t
            acc[1] += n
        return tot_t, tot_r, by_ns

    pt, pr, pns = totals(prev)
    ct, cr, cns = totals(cur)
    providers = []
    for ns in set(pns) | set(cns):
        p_tok = pns.get(ns, [0, 0])[0]
        c_tok = cns.get(ns, [0, 0])[0]
        providers.append({
            "namespace": ns,
            "token_share_prev": p_tok / pt if pt else None,
            "token_share_cur": c_tok / ct if ct else None,
            "request_share_prev": pns.get(ns, [0, 0])[1] / pr if pr else None,
            "request_share_cur": cns.get(ns, [0, 0])[1] / cr if cr else None,
            "tokens_cur": c_tok,
        })
    providers.sort(key=lambda p: -(p["token_share_cur"] or 0))
    return {
        "available": True,
        "prev_date": prev,
        "cur_date": cur,
        "tokens_prev": pt,
        "tokens_cur": ct,
        "requests_prev": pr,
        "requests_cur": cr,
        "tokens_per_request_prev": pt / pr if pr else None,
        "tokens_per_request_cur": ct / cr if cr else None,
        "providers": providers[: min(top_n, MARKET_PROVIDER_CAP)],
    }


def _build_panel(
    conn: sqlite3.Connection, covered_clean: list[str], top_n: int
) -> dict[str, Any]:
    days = covered_clean[-PANEL_DAYS:]
    if len(days) < 4:
        return {"available": False, "reason": "fewer than four clean covered days",
                "days": days}
    half = len(days) // 2
    prev_days, last_days = days[:half], days[half:]
    series = _slug_rows_in(conn, days)
    # cohort: present (with supplement coverage) on every clean day in the window
    cohort = [s for s, v in series.items() if all(d in v for d in days)]

    models = []
    for slug in cohort:
        last = [series[slug][d] for d in last_days]
        prev = [series[slug][d] for d in prev_days]
        lt, lc = _avg(last, "prompt"), _avg(last, "completion")
        pt, pc = _avg(prev, "prompt"), _avg(prev, "completion")
        lr, lrp = _avg(last, "requests"), _avg(prev, "requests")
        last_tok = (lt or 0) + (lc or 0)
        prev_tok = (pt or 0) + (pc or 0)
        models.append({
            "slug": slug,
            "tokens_last": last_tok,
            "tokens_prev": prev_tok,
            "tokens_delta": (last_tok - prev_tok) / prev_tok if prev_tok else None,
            "requests_last": lr,
            "requests_delta": (lr - lrp) / lrp if lr is not None and lrp else None,
            "tokens_per_request_last": last_tok / lr if lr else None,
            # cache rate = cached / prompt (same window); reasoning = reasoning / completion
            "cache_rate_last": _avg(last, "cached") / lt if lt else None,
            "reasoning_rate_last": _avg(last, "reasoning") / lc if lc else None,
        })
    models.sort(key=lambda m: -(m["tokens_delta"] if m["tokens_delta"] is not None else -9))
    return {
        "available": True,
        "prev_days": prev_days,
        "last_days": last_days,
        "cohort_size": len(models),
        "models": models[:top_n],
    }


def _build_newcomers(
    conn: sqlite3.Connection, covered_clean: list[str], top_n: int
) -> list[dict[str, Any]]:
    if not covered_clean:
        return []
    anchor = covered_clean[-1]
    cutoff = (
        datetime.strptime(anchor, "%Y-%m-%d") - timedelta(days=NEWCOMER_LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")
    cur = conn.execute(
        """SELECT variant_permaslug, MIN(substr(date,1,10)) AS first_seen,
                  MAX(substr(date,1,10)) AS last_seen
        FROM model_activity WHERE variant = 'standard'
          AND substr(date,1,10) <= ?
        GROUP BY variant_permaslug HAVING first_seen >= ? ORDER BY first_seen""",
        (anchor, cutoff),
    )
    out = []
    for slug, first, last in cur.fetchall():
        last_row = conn.execute(
            """SELECT total_prompt_tokens + total_completion_tokens, count
            FROM model_activity WHERE variant = 'standard' AND variant_permaslug = ?
              AND substr(date,1,10) = ?""",
            (slug, last),
        ).fetchone()
        first_row = conn.execute(
            """SELECT total_prompt_tokens + total_completion_tokens
            FROM model_activity WHERE variant = 'standard' AND variant_permaslug = ?
              AND substr(date,1,10) = ?""",
            (slug, first),
        ).fetchone()
        out.append({
            "slug": slug,
            "first_seen": first,
            "last_seen": last,
            "tokens_first": first_row[0] if first_row else None,
            "tokens_last": last_row[0] if last_row else None,
            "requests_last": last_row[1] if last_row else None,
        })
    out.sort(key=lambda x: -(x["tokens_last"] or 0))
    return out[:top_n]


def _build_anomalies(
    conn: sqlite3.Connection, covered_clean: list[str]
) -> list[dict[str, Any]]:
    if not covered_clean:
        return []
    latest = covered_clean[-1]
    rows = conn.execute(
        """SELECT m.variant_permaslug, m.total_prompt_tokens, m.total_completion_tokens,
                  m.total_native_tokens_cached, m.count
        FROM model_activity m
        JOIN activity_coverage ac
          ON m.date = ac.date AND m.variant_permaslug = ac.variant_permaslug
         AND m.variant = ac.variant
        WHERE m.variant = 'standard' AND ac.supplement_seen = 1
          AND substr(m.date,1,10) = ?""",
        (latest,),
    ).fetchall()
    anomalies = []
    for slug, p, comp, cached, n in rows:
        p = p or 0
        comp = comp or 0
        cached = cached or 0
        n = n or 0
        cache_rate = cached / p if p else None
        tpr = (p + comp) / n if n else None
        reasons = []
        if cache_rate is not None and cache_rate > CACHE_HIGH:
            reasons.append("cache_rate_above_98pct")
        if cache_rate is not None and cache_rate < CACHE_LOW:
            reasons.append("cache_rate_below_5pct")
        if not reasons and cache_rate is not None and tpr is not None and tpr > 200_000:
            reasons.append("tokens_per_request_outlier")
        if reasons:
            anomalies.append({
                "slug": slug,
                "date": latest,
                "cache_rate": cache_rate,
                "tokens_per_request": tpr,
                "requests": n,
                "reasons": reasons,
            })
    anomalies.sort(key=lambda a: -(a["requests"] or 0))
    return anomalies


def _build_candidates(
    newcomers: list[dict], panel: dict[str, Any], anomalies: list[dict]
) -> list[dict[str, Any]]:
    cands: dict[str, dict] = {}

    def add(slug: str, reason: str, weight: float, question: str) -> None:
        e = cands.setdefault(
            slug, {"slug": slug, "reasons": [], "weight": 0.0, "suggested_query": question}
        )
        e["reasons"].append(reason)
        e["weight"] = max(e["weight"], weight)

    for a in anomalies:
        add(a["slug"], "anomaly:" + ",".join(a["reasons"]), float(a.get("requests") or 0),
            f"What is OpenRouter model {a['slug']}? Who releases it and what is it for?")
    if panel.get("available"):
        for m in panel["models"]:
            if m["tokens_delta"] and m["tokens_delta"] > 0.3:
                add(m["slug"], "panel_gainer", float(m["tokens_last"] or 0),
                    f"What is OpenRouter model {m['slug']}? Who releases it and what is it for?")
    for n in newcomers:
        add(n["slug"], "newcomer", float(n["tokens_last"] or 0),
            f"What is OpenRouter model {n['slug']}? Announcement date and purpose?")

    out = sorted(cands.values(), key=lambda c: -c["weight"])
    for c in out:
        c.pop("weight", None)
    return out[:8]


# ── markdown render ─────────────────────────────────────────────────


def render_markdown(packet: dict[str, Any]) -> str:
    q = packet["quality"]
    lines: list[str] = []
    lines.append(f"# OpenRouter Weekly Digest Packet — {packet['generated_at']}")
    lines.append("")
    lines.append(f"generated_at: {packet['generated_at']}")
    lines.append(f"top_n: {packet['top_n']}")
    lines.append("")

    lines.append("## A. Data quality gate")
    lines.append("")
    lines.append(
        f"- newest date in DB: {q['newest_date']} "
        f"({'clean whole-market day' if q['newest_is_market_day'] else 'partial top-N panel, excluded from trends'})"
    )
    lines.append(f"- clean whole-market days: {', '.join(q['market_days']) or '-'}")
    lines.append(f"- clean covered days total: {q['clean_covered_day_count']}; "
                 f"panel window ({len(q['panel_days_used'])} days): "
                 f"{', '.join(q['panel_days_used']) or '-'}")
    lines.append(f"- contaminated days (cached > prompt, dropped): "
                 f"{', '.join(q['contaminated_days']) or 'none'}")
    for w in q["warnings"]:
        lines.append(f"- WARNING: {w}")
    lines.append("")

    m = packet["market"]
    lines.append("## B. Whole-market totals and rotation (clean market days only)")
    lines.append("")
    if not m.get("available"):
        lines.append(f"- unavailable: {m.get('reason')}")
    else:
        lines.append(
            f"- {m['prev_date']} -> {m['cur_date']}: "
            f"tokens {_fmt_b(m['tokens_prev'])} -> {_fmt_b(m['tokens_cur'])} "
            f"({_fmt_delta((m['tokens_cur'] - m['tokens_prev']) / m['tokens_prev'] if m['tokens_prev'] else None)}), "
            f"requests {_fmt_req(m['requests_prev'])} -> {_fmt_req(m['requests_cur'])}"
        )
        lines.append(f"- tokens/request {m['tokens_per_request_prev']:,.0f} -> "
                     f"{m['tokens_per_request_cur']:,.0f}")
        lines.append("")
        lines.append("| namespace | token share | request share | tokens cur |")
        lines.append("|---|---:|---:|---:|")
        for p in m["providers"]:
            lines.append(
                f"| {p['namespace']} | {_fmt_pct(p['token_share_prev'])} -> "
                f"{_fmt_pct(p['token_share_cur'])} | {_fmt_pct(p['request_share_prev'])} -> "
                f"{_fmt_pct(p['request_share_cur'])} | {_fmt_b(p['tokens_cur'])} |"
            )
    lines.append("")

    p = packet["panel"]
    lines.append("## C. Top-N panel (cache/reasoning complete only here)")
    lines.append("")
    if not p.get("available"):
        lines.append(f"- unavailable: {p.get('reason')}")
    else:
        lines.append(f"- prev window: {', '.join(p['prev_days'])}")
        lines.append(f"- last window: {', '.join(p['last_days'])}")
        lines.append(f"- fixed cohort: {p['cohort_size']} models with supplement coverage "
                     f"on every clean day (showing top {len(p['models'])})")
        lines.append("")
        lines.append("| slug | tokens last | delta | requests last | cache/prompt | reasoning/comp | tok/req |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for mod in p["models"]:
            tpr = mod["tokens_per_request_last"]
            tpr_s = f"{tpr:,.0f}" if tpr is not None else "-"
            lines.append(
                f"| {mod['slug']} | {_fmt_b(mod['tokens_last'])} | "
                f"{_fmt_delta(mod['tokens_delta'])} | {_fmt_req(mod['requests_last'])} | "
                f"{_fmt_pct(mod['cache_rate_last'])} | {_fmt_pct(mod['reasoning_rate_last'])} | "
                f"{tpr_s} |"
            )
    lines.append("")

    lines.append("## D. Newcomers and anomalies")
    lines.append("")
    lines.append("Newcomers (first seen within lookback, top by last clean day tokens):")
    if not packet["newcomers"]:
        lines.append("- none")
    for n in packet["newcomers"][:12]:
        lines.append(
            f"- {n['slug']}: first {n['first_seen']} ({_fmt_b(n['tokens_first'])} tokens) "
            f"-> {n['last_seen']} ({_fmt_b(n['tokens_last'])} tokens), "
            f"{_fmt_req(n['requests_last'])} requests"
        )
    if len(packet["newcomers"]) > 12:
        lines.append(f"- ... {len(packet['newcomers']) - 12} more (see JSON)")
    lines.append("")
    lines.append("Shape anomalies (latest clean covered day, top by requests):")
    if not packet["anomalies"]:
        lines.append("- none")
    for a in packet["anomalies"][:15]:
        lines.append(
            f"- {a['slug']} [{', '.join(a['reasons'])}]: "
            f"cache/prompt {_fmt_pct(a['cache_rate'])}, "
            f"{a['tokens_per_request']:,.0f} tok/req, {_fmt_req(a['requests'])} requests"
        )
    if len(packet["anomalies"]) > 15:
        lines.append(f"- ... {len(packet['anomalies']) - 15} more (see JSON)")
    lines.append("")

    lines.append("## E. External enrichment candidates")
    lines.append("")
    if not packet["enrichment_candidates"]:
        lines.append("- none")
    for c in packet["enrichment_candidates"]:
        lines.append(f"- {c['slug']} ({', '.join(c['reasons'])}) :: {c['suggested_query']}")
    lines.append("")
    return "\n".join(lines)
