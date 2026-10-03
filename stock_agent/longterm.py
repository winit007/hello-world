"""Long-term picks: stocks to buy and hold for about a year, ranked by their historical chance of a gain.

The ranking is the model fund's factor score (risk-adjusted 12-1 momentum plus low volatility, Nifty 200).
To put a chance of winning on a rank, the score is replayed at every month end of the last ten years
using only data up to that day. For the stocks in each rank band (1-5, 6-10, ...) the history records how
often the price was higher 12 months later, how often the stock beat the Nifty 50 ETF, the median 12-month
return and the bad case (10th percentile). The rank barely separates the bands, so each pick's chance
of winning is the average of two records: its rank band's, and the stock's own (how often it was higher
12 months later, measured at every month end of its history). The 20 best-scored stocks are ordered by
that chance and the top ones shown, most likely to win first.

The same caveat as the model fund applies: the universe is today's Nifty 200, the companies that
survived, so the historical chances are flattering. The "any stock in the list" base rate shows how much
of that is just survivorship.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .data import DEFAULT_CACHE
from .fund import FundRules, _rebalance_dates, load, scores, select

HOLD_DAYS = 252                                  # about 12 months of trading days
BANDS = [(1, 5), (6, 10), (11, 20), (21, 35), (36, 60)]


def _band(rank: int) -> tuple[int, int] | None:
    return next((b for b in BANDS if b[0] <= rank <= b[1]), None)


def history(closes: pd.DataFrame, values: pd.DataFrame, bench: pd.Series, rules: FundRules, progress=None) -> dict:
    """12-month outcomes of stocks by their score rank, from every month end with a full year after it."""
    progress = progress or (lambda m, f: None)
    pos = {d: i for i, d in enumerate(closes.index)}
    bench = bench.reindex(closes.index).ffill()
    dates = [d for d in _rebalance_dates(closes.index, "M")
             if len(closes.loc[:d]) >= 260 and pos[d] + HOLD_DAYS < len(closes)]
    rows = []
    for k, d in enumerate(dates):
        sc = scores(closes, values, d, rules)
        if sc.empty:
            continue
        i = pos[d]
        start, end = closes.iloc[i], closes.iloc[i + HOLD_DAYS]
        nifty = float(bench.iloc[i + HOLD_DAYS] / bench.iloc[i] - 1)
        for rank, t in enumerate(sc.index, 1):
            if pd.notna(start[t]) and pd.notna(end[t]) and start[t] > 0:
                rows.append((d, rank, float(end[t] / start[t] - 1), nifty))
        progress(f"Replaying the ranking: {d:%b %Y}", 0.45 + 0.45 * (k + 1) / len(dates))
    df = pd.DataFrame(rows, columns=["date", "rank", "ret", "nifty"])
    out = {"from": str(dates[0].date()) if dates else None, "to": str(dates[-1].date()) if dates else None,
           "months": len(dates), "bands": {}, "all": None}

    def summary(x: pd.DataFrame) -> dict | None:
        if x.empty:
            return None
        r = x["ret"]
        return {"n": int(len(r)), "win": float((r > 0).mean()), "beat_nifty": float((r > x["nifty"]).mean()),
                "median": float(r.median()), "mean": float(r.mean()), "p10": float(r.quantile(0.10)),
                "worst": float(r.min())}

    for lo, hi in BANDS:
        out["bands"][f"{lo}-{hi}"] = summary(df[(df["rank"] >= lo) & (df["rank"] <= hi)])
    out["all"] = summary(df)
    nifty = df.drop_duplicates("date")["nifty"] if not df.empty else pd.Series(dtype=float)
    out["nifty"] = {"win": float((nifty > 0).mean()), "median": float(nifty.median())} if len(nifty) else None
    return out


def own_record(close: pd.Series) -> tuple[float | None, int]:
    """Share of month ends after which this stock was higher 12 months later, and how many month ends."""
    c = close.dropna()
    idx = {d: i for i, d in enumerate(c.index)}
    ends = [d for d in _rebalance_dates(c.index, "M") if idx[d] + HOLD_DAYS < len(c)]
    if len(ends) < 12:
        return None, len(ends)
    fwd = [c.iloc[idx[d] + HOLD_DAYS] / c.iloc[idx[d]] - 1 for d in ends]
    return float(np.mean([f > 0 for f in fwd])), len(ends)


def _next_review(d: date) -> date:
    """When to check the ranking again: the end of this calendar quarter, or of the next one if this
    quarter ends within a month."""
    end = (pd.Timestamp(d) + pd.offsets.QuarterEnd(0)).date()
    if (end - d).days < 30:
        end = (pd.Timestamp(end) + pd.offsets.QuarterEnd(1)).date()
    return end


def run(capital: float = 200_000, top: int = 10, offline: bool = False, cache_dir=DEFAULT_CACHE, progress=None,
        rules: FundRules | None = None, data=None) -> dict:
    progress = progress or (lambda m, f: None)
    rules = rules or FundRules(universe="nifty200", size=max(top, 10))
    closes, values, bench, sectors = data or load(rules, offline, cache_dir, progress)
    hist = history(closes, values, bench, rules, progress)
    today = closes.index[-1]
    sc = scores(closes, values, today, rules)
    if sc.empty:
        raise ValueError("Not enough price history to rank the stocks")
    chosen = select(sc, sectors, FundRules(**{**rules.__dict__, "size": max(2 * top, 20)}))   # candidates
    rank_of = {t: i for i, t in enumerate(sc.index, 1)}
    slot = capital / max(len(chosen), 1)
    hold_until = (today + pd.Timedelta(days=365)).date()
    picks = []
    for t in chosen:
        px = float(closes[t].dropna().iloc[-1])
        rank = rank_of[t]
        b = _band(rank)
        st = hist["bands"].get(f"{b[0]}-{b[1]}") if b else None
        own, own_n = own_record(closes[t])
        band_win = st["win"] if st else None
        parts = [x for x in (band_win, own) if x is not None]
        shares = int(slot // px)
        sma200 = float(closes[t].dropna().iloc[-200:].mean())
        picks.append({
            "ticker": t, "industry": sectors.get(t, "Other"), "price": px, "rank": rank,
            "band": f"{b[0]}-{b[1]}" if b else None, "win_chance": float(np.mean(parts)) if parts else None,
            "band_win": band_win, "own_win": own, "own_months": own_n,
            "beat_nifty": st["beat_nifty"] if st else None, "median_return": st["median"] if st else None,
            "bad_case": st["p10"] if st else None, "sample": st["n"] if st else 0,
            "typical_price": px * (1 + st["median"]) if st else None, "bad_price": px * (1 + st["p10"]) if st else None,
            "momentum_12_1": float(sc.loc[t, "momentum_12_1"]), "volatility": float(sc.loc[t, "volatility"]),
            "score": float(sc.loc[t, "score"]), "above_200dma": px > sma200, "sma200": sma200,
            "shares": shares, "amount": shares * px, "too_dear": shares == 0,
        })
    picks.sort(key=lambda p: (p["win_chance"] or 0, p["score"]), reverse=True)
    picks = picks[:top]
    slot = capital / max(len(picks), 1)
    for p in picks:                      # size the shown picks: an equal share of the capital each
        p["shares"] = int(slot // p["price"])
        p["amount"] = p["shares"] * p["price"]
        p["too_dear"] = p["shares"] == 0
    progress("Done", 1.0)
    return {"as_of": str(today.date()), "capital": capital, "slot": slot, "hold_until": str(hold_until),
            "review": str(_next_review(today.date())), "universe": rules.universe, "universe_size": int(len(sc)),
            "picks": picks, "history": hist}


def render_text(res: dict) -> str:
    from .sizing import money

    h = res["history"]
    lines = [f"Long-term picks (about 12 months) · data to {res['as_of']} · {money(res['slot'], '₹')} per stock",
             f"Chances come from replaying the ranking every month {h['from']} to {h['to']}; most likely to win first."]
    for i, p in enumerate(res["picks"], 1):
        ch = f"{p['win_chance']:.0%}" if p["win_chance"] is not None else "n/a"
        lines.append(f"{i:2}. {p['ticker'].replace('.NS', ''):<12} ₹{p['price']:,.2f}  chance of a gain in 12 months {ch} "
                     f"(own record {p['own_win']:.0%}, rank {p['rank']} band {p['band_win']:.0%}; typical "
                     f"{p['median_return']:+.0%}, bad case {p['bad_case']:+.0%})  "
                     f"buy {p['shares']} shares = {money(p['amount'], '₹')}")
    a = h["all"]
    if h.get("nifty"):
        lines.append(f"Nifty 50 index fund: higher after 12 months {h['nifty']['win']:.0%} of the time "
                     f"(median {h['nifty']['median']:+.0%}).")
    if a:
        lines.append(f"Any stock in the list: higher after 12 months {a['win']:.0%} of the time (median {a['median']:+.0%}). "
                     "Today's index members survived, so all these figures flatter reality.")
    return "\n".join(lines)
