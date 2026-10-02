"""New listings on NSE and IPO news.

The NSE equity list carries each company's listing date, so "new IPOs" are the stocks first listed in the
last N days. That set also contains a few non-IPO listings (demergers, relistings, migrations from the SME
platform); the table says which series each trades in. For each one we show the listing-day open (the
listing price), the latest close and how it has moved since. Upcoming and open IPOs are not in that file,
so the latest IPO headlines are collected from Google News and scored like stock news.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from .data import DEFAULT_CACHE, _cache_path, read_cached, refresh_many
from .news import Headline, fetch_headlines, score
from .universes import recent_listings

IPO_QUERIES = ["upcoming IPO India", "IPO subscription status NSE", "IPO listing gain NSE"]


def listings(days: int = 90, offline: bool = False, cache_dir: Path = DEFAULT_CACHE, progress=None) -> pd.DataFrame:
    """Recent NSE listings with listing price, latest price and performance since listing."""
    rec = recent_listings(days, offline)
    if rec.empty:
        return rec
    tickers = [s + ".NS" for s in rec["symbol"]]
    if not offline:
        refresh_many(tickers, "1y", "1d", cache_dir, progress=progress)
    rows = []
    for (_, r), t in zip(rec.iterrows(), tickers):
        path = _cache_path(cache_dir, t, "1y", "1d")
        base = {"symbol": r["symbol"], "ticker": t, "name": r["name"], "series": r["series"], "listed": r["listed"].date()}
        if not path.exists():
            rows.append({**base, "status": "no price data yet"})
            continue
        df = read_cached(path)
        df = df[df.index >= pd.Timestamp(r["listed"])]  # ignore any older history of a relisted symbol
        if df.empty:
            rows.append({**base, "status": "no price data yet"})
            continue
        first, last = df.iloc[0], df.iloc[-1]
        high = float(df["High"].max())
        turnover = float((df["Close"] * df["Volume"]).tail(20).median())
        rows.append({**base, "status": "trading", "listing_open": round(float(first["Open"]), 2),
                     "listing_close": round(float(first["Close"]), 2), "last": round(float(last["Close"]), 2),
                     "last_date": df.index[-1].date(),
                     "day1_pct": float(first["Close"] / first["Open"] - 1),
                     "since_listing_pct": float(last["Close"] / first["Open"] - 1),
                     "from_high_pct": float(last["Close"] / high - 1), "high": round(high, 2),
                     "sessions": int(len(df)), "turnover": turnover})
    return pd.DataFrame(rows)


def ipo_news(offline: bool = False, cache_dir: Path = DEFAULT_CACHE, limit: int = 30) -> list[Headline]:
    """Latest IPO headlines (upcoming issues, subscription, listings), newest first; cached for the day."""
    path = cache_dir / "news" / "_IPO.json"
    if path.exists() and date.fromtimestamp(path.stat().st_mtime) == date.today() or offline:
        if path.exists():
            return [Headline(**d) for d in json.loads(path.read_text(encoding="utf-8"))]
        return []
    seen, out = set(), []
    for q in IPO_QUERIES:
        try:
            for h in fetch_headlines("IPO", q, limit=40):
                k = h.title.lower()
                if k not in seen:
                    seen.add(k)
                    out.append(h)
        except Exception:
            continue
    out = score(sorted(out, key=lambda h: h.published, reverse=True)[:limit])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([h.__dict__ for h in out], indent=1, ensure_ascii=False), encoding="utf-8")
    return out


def render_text(df: pd.DataFrame, news: list[Headline], days: int) -> str:
    lines = [f"New NSE listings in the last {days} days: {len(df)}"]
    if not df.empty and "since_listing_pct" in df:
        t = df[df["status"] == "trading"]
        if len(t):
            lines.append(f"Average move since listing {t['since_listing_pct'].mean():+.1%}; "
                         f"{(t['since_listing_pct'] > 0).sum()} of {len(t)} are above their listing price.")
    lines.append("")
    lines.append(f"{'Listed':<11} {'Symbol':<12} {'Listing':>9} {'Now':>9} {'Since':>7} {'From high':>9}  Name")
    for _, r in df.iterrows():
        if r.get("status") != "trading":
            lines.append(f"{r['listed']!s:<11} {r['symbol']:<12} {'':>9} {'':>9} {'':>7} {'':>9}  {r['name']} (no price data yet)")
            continue
        lines.append(f"{r['listed']!s:<11} {r['symbol']:<12} {r['listing_open']:>9.2f} {r['last']:>9.2f} "
                     f"{r['since_listing_pct']:>+7.1%} {r['from_high_pct']:>+9.1%}  {r['name']}"
                     f"{'' if r['series'] == 'EQ' else ' [' + r['series'] + ']'}")
    if news:
        lines += ["", "IPO news (upcoming issues, subscriptions, listings):"]
        for h in news[:15]:
            lines.append(f"  {h.published or 'undated'}  {h.sentiment:+.2f}  {h.title} ({h.source})")
    return "\n".join(lines)
