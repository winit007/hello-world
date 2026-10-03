"""My screener: pick stocks with your own rules, see today's matches, and backtest the rules.

Every metric is computed from daily prices up to the day in question only, so the backtest never uses the
future: on each rebalance date the filters and ranking are applied to that day's values, the top N are held in
equal weight until the next rebalance, and every trade pays real costs for your capital (costs.py: charges,
DP fee, spread and impact). Compared with the Nifty 50 ETF
and with holding every stock of the universe in equal weight (which shows how much is just survivorship:
today's index members are the companies that survived).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import attribution as A
from . import costs as C
from . import fundamentals as F
from . import pit
from . import sip as S
from .fund import REBALANCE, _metrics, _rebalance_dates

METRICS = {
    "price": ("Price (₹)", "level"),
    "ret_1m": ("Return, 1 month", "pct"),
    "ret_3m": ("Return, 3 months", "pct"),
    "ret_6m": ("Return, 6 months", "pct"),
    "ret_12m": ("Return, 12 months", "pct"),
    "from_high": ("Distance from 52-week high", "pct"),
    "vs_sma50": ("Price vs 50-day average", "pct"),
    "vs_sma200": ("Price vs 200-day average", "pct"),
    "sma50_vs_200": ("50-day vs 200-day average", "pct"),
    "rsi14": ("RSI (14 days)", "level"),
    "vol_1y": ("Volatility, 1 year", "pct"),
    "max_dd_1y": ("Worst fall in the last year", "pct"),
    "beta_1y": ("Beta to Nifty, 1 year", "level"),
    "value_cr": ("Traded value a day (₹ crore)", "level"),
}
PRICE_METRICS = list(METRICS)
METRICS.update(F.METRICS)                 # company results: P/E, ROE, debt, growth, promoter holding ...
FUNDAMENTAL = set(F.METRICS)

PRESETS = {
    "Momentum leaders": {"filters": [{"metric": "vs_sma200", "op": ">", "value": 0}, {"metric": "value_cr", "op": ">", "value": 20}],
                         "rank_by": "ret_6m", "descending": True},
    "Near 52-week high": {"filters": [{"metric": "from_high", "op": ">", "value": -0.05}, {"metric": "ret_12m", "op": ">", "value": 0.2}],
                          "rank_by": "ret_3m", "descending": True},
    "Pullback in an uptrend": {"filters": [{"metric": "vs_sma200", "op": ">", "value": 0}, {"metric": "rsi14", "op": "<", "value": 40}],
                               "rank_by": "ret_12m", "descending": True},
    "Low volatility": {"filters": [{"metric": "beta_1y", "op": "<", "value": 0.9}],
                       "rank_by": "vol_1y", "descending": False},
    "Quality at a fair price": {"filters": [{"metric": "roe", "op": ">", "value": 0.15}, {"metric": "de", "op": "<", "value": 0.5},
                                            {"metric": "pe", "op": "<", "value": 40}],
                                "rank_by": "roe", "descending": True},
    "Growth with momentum": {"filters": [{"metric": "eps_growth", "op": ">", "value": 0.15}, {"metric": "sales_growth", "op": ">", "value": 0.1},
                                         {"metric": "vs_sma200", "op": ">", "value": 0}],
                             "rank_by": "ret_6m", "descending": True},
}


@dataclass
class Screen:
    filters: list[dict] = field(default_factory=list)      # [{"metric", "op": ">"/"<", "value"}]
    rank_by: str = "ret_6m"
    descending: bool = True
    top: int = 10
    rebalance: str = "Q"

    def uses_fundamentals(self) -> bool:
        return bool(({f["metric"] for f in self.filters} | {self.rank_by}) & FUNDAMENTAL)

    def validate(self) -> None:
        for f in self.filters:
            if f.get("metric") not in METRICS or f.get("op") not in (">", "<"):
                raise ValueError(f"Bad rule: {f}")
            float(f["value"])
        if self.rank_by not in METRICS:
            raise ValueError("Unknown ranking metric")
        if not 1 <= int(self.top) <= 50:
            raise ValueError("Hold between 1 and 50 stocks")
        if self.rebalance not in REBALANCE:
            raise ValueError("Rebalance monthly, quarterly, half-yearly or yearly")


def _rsi(c: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.where(dn > 0))
    return rsi.mask((dn == 0) & up.notna(), 100.0)


def metric_frames(closes: pd.DataFrame, values: pd.DataFrame, bench: pd.Series,
                  fund_data: dict | None = None) -> dict[str, pd.DataFrame]:
    """Every metric for every stock and day, each value using only data up to that day."""
    r = closes.pct_change()
    br = bench.reindex(closes.index).ffill().pct_change()
    sma50, sma200 = closes.rolling(50, min_periods=45).mean(), closes.rolling(200, min_periods=180).mean()
    high = closes.rolling(252, min_periods=200).max()
    cov = r.rolling(252, min_periods=200).cov(br)
    var = br.rolling(252, min_periods=200).var()
    dd = closes / closes.rolling(252, min_periods=200).max() - 1
    out = {
        "price": closes,
        "ret_1m": closes / closes.shift(21) - 1, "ret_3m": closes / closes.shift(63) - 1,
        "ret_6m": closes / closes.shift(126) - 1, "ret_12m": closes / closes.shift(252) - 1,
        "from_high": closes / high - 1, "vs_sma50": closes / sma50 - 1, "vs_sma200": closes / sma200 - 1,
        "sma50_vs_200": sma50 / sma200 - 1, "rsi14": _rsi(closes),
        "vol_1y": r.rolling(252, min_periods=200).std() * math.sqrt(252),
        "max_dd_1y": dd.rolling(252, min_periods=200).min(),
        "beta_1y": cov.div(var, axis=0),
        "value_cr": values.rolling(60, min_periods=40).median() / 1e7,
    }
    if fund_data is not None:
        out.update(F.frames(closes, fund_data))
    return out


def snapshot(frames: dict[str, pd.DataFrame], date, allowed=None) -> pd.DataFrame:
    rows = {k: f.loc[:date].iloc[-1] for k, f in frames.items()}
    snap = pd.DataFrame(rows).dropna(subset=["price", "ret_12m", "vol_1y"])
    return snap if allowed is None else snap[snap.index.isin(allowed)]


def apply(snap: pd.DataFrame, sc: Screen) -> pd.DataFrame:
    ok = pd.Series(True, index=snap.index)
    for f in sc.filters:
        col = snap[f["metric"]]
        ok &= (col > float(f["value"])) if f["op"] == ">" else (col < float(f["value"]))
    return snap[ok & snap[sc.rank_by].notna()].sort_values(sc.rank_by, ascending=not sc.descending)


def backtest(closes: pd.DataFrame, frames: dict[str, pd.DataFrame], bench: pd.Series, sc: Screen, progress=None,
             values: pd.DataFrame | None = None, capital: float = 200_000, per_order: float = 0.0,
             pit_n: int | None = None) -> dict:
    daily = closes.pct_change()
    dates = [d for d in _rebalance_dates(closes.index, sc.rebalance) if len(closes.loc[:d]) >= 260]
    fund_from = None
    if sc.uses_fundamentals():           # company results only go back a few years: start when most stocks have them
        used = ({f["metric"] for f in sc.filters} | {sc.rank_by}) & FUNDAMENTAL
        starts = [F.coverage_start(frames[m], dates) for m in used]
        if any(x is None for x in starts):
            raise ValueError("Too few companies have these results yet to backtest the rules")
        fund_from = max(starts)
        dates = [d for d in dates if d >= fund_from]
    if len(dates) < 3:
        raise ValueError("Not enough history to backtest" + (f": company results start {fund_from:%b %Y}" if fund_from is not None else ""))
    nav, ew, bn, held, hits, beats, turns = [1.0], [1.0], [1.0], [], [], [], []
    seg_s, seg_e, seg_b, seg_g = [], [], [], []   # daily values, so volatility and worst fall see every day
    gross = [1.0]                              # the same picks with no trading costs
    prev: dict[str, float] = {}
    log = []
    values = values if values is not None else closes * 0 + 1e9
    cost_tot = {"charges": 0.0, "spread_impact": 0.0}        # per rupee at the start
    for k, (d0, d1) in enumerate(zip(dates[:-1], dates[1:])):
        if progress and k % 6 == 0:
            progress(f"Backtesting {d0:%b %Y}", 0.4 + 0.5 * k / len(dates))
        snap = snapshot(frames, d0, pit.members(closes, values, d0, pit_n) if pit_n else None)
        picks = list(apply(snap, sc).index[: int(sc.top)])
        period = daily.loc[(daily.index > d0) & (daily.index <= d1)]
        grow_all = (1 + period[snap.index].fillna(0)).prod()
        b = float(bench.loc[:d1].iloc[-1] / bench.loc[:d0].iloc[-1] - 1)
        if picks:
            target = C.with_band(prev, {t: 1 / len(picks) for t in picks})
            traded = sum(abs(target.get(t, 0) - prev.get(t, 0)) for t in set(target) | set(prev))
            adv, dvol = C.liquidity(closes, values, d0)
            cc = C.rebalance_cost(prev, target, capital * nav[-1], adv, dvol, per_order)
            for k_ in cost_tot:
                cost_tot[k_] += cc[k_] / capital
            g = grow_all[picks]
            w = pd.Series(target)[picks]
            r = float((g * w).sum() - 1) - cc["fraction"]
            path = (1 + period[picks].fillna(0)).cumprod().mul(w).sum(axis=1) * (1 - cc["fraction"])
            gpath = (1 + period[picks].fillna(0)).cumprod().mul(w).sum(axis=1)
            r_g = float((g * w).sum() - 1)
            hits += [float(x) > 1 for x in g]
            beats += [float(x) - 1 > b for x in g]
            drift = g * w / (g * w).sum()
            prev = drift.to_dict()
            turns.append(traded / 2)
        else:                                   # nothing passed the rules: sell everything, sit in cash
            adv, dvol = C.liquidity(closes, values, d0)
            cc = C.rebalance_cost(prev, {}, capital * nav[-1], adv, dvol, per_order)
            for k_ in cost_tot:
                cost_tot[k_] += cc[k_] / capital
            r = -cc["fraction"]
            prev = {}
            path = pd.Series(1.0 + r, index=period.index)
            gpath, r_g = pd.Series(1.0, index=period.index), 0.0
        seg_s.append(path * nav[-1])
        seg_g.append(gpath * gross[-1])
        gross.append(gross[-1] * (1 + r_g))
        seg_e.append((1 + period[snap.index].fillna(0)).cumprod().mean(axis=1) * ew[-1])
        bseg = bench.reindex(closes.index).ffill()
        seg_b.append(bseg.loc[period.index] / bseg.loc[:d0].iloc[-1] * bn[-1])
        nav.append(nav[-1] * (1 + r))
        ew.append(ew[-1] * (1 + float(grow_all.mean() - 1)))
        bn.append(bn[-1] * (1 + b))
        held.append(len(picks))
        log.append({"date": str(d0.date()), "picks": picks})
    start = pd.DataFrame({"screen": [1.0], "equal_weight": [1.0], "nifty": [1.0]}, index=[dates[0]])
    daily_curve = pd.concat([start, pd.DataFrame({"screen": pd.concat(seg_s), "equal_weight": pd.concat(seg_e),
                                                  "nifty": pd.concat(seg_b)})])
    gross_curve = pd.concat([pd.Series([1.0], index=[dates[0]]), pd.concat(seg_g)])
    curve = daily_curve.resample("W-FRI").last().dropna()      # weekly points for the chart
    yearly = []
    for y, g in daily_curve.groupby(daily_curve.index.year):
        prev_row = daily_curve[daily_curve.index < g.index[0]].tail(1)
        base = prev_row.iloc[0] if len(prev_row) else g.iloc[0]
        yearly.append({"year": int(y), **{c: float(g[c].iloc[-1] / base[c] - 1) for c in daily_curve.columns}})
    per_year = REBALANCE[sc.rebalance][1]
    return {"curve": [{"date": str(d.date()), **{c: float(v) for c, v in row.items()}} for d, row in curve.iterrows()],
            "metrics": {c: _metrics(daily_curve[c], 252) for c in daily_curve.columns},
            "hit_rate": float(np.mean(hits)) if hits else None, "beat_nifty_rate": float(np.mean(beats)) if beats else None,
            "avg_held": float(np.mean(held)) if held else 0.0, "periods_in_cash": int(sum(1 for h in held if h == 0)),
            "turnover_per_year": float(np.mean(turns) * per_year) if turns else 0.0, "yearly": yearly,
            "last_picks": log[-1]["picks"] if log else [],
            "costs": {**cost_tot, "capital": capital}, "daily": daily_curve,
            "fundamentals_from": str(fund_from.date()) if fund_from is not None else None,
            "attribution": A.compare(daily_curve["screen"], gross_curve, daily_curve["equal_weight"], daily_curve["nifty"])}


def run(closes: pd.DataFrame, values: pd.DataFrame, bench: pd.Series, sc: Screen, capital: float = 200_000,
        sectors: dict | None = None, progress=None, per_order: float = 0.0, benchmark: str = A.DEFAULT,
        pit_n: int | None = None, fund_data: dict | None = None, cache_dir=None, offline: bool = False,
        backtest_too: bool = True) -> dict:
    """`fund_data` (company results for the whole list) is needed when the rules use them; otherwise results are
    fetched for today's picks only, to show next to them (with `cache_dir`)."""
    sc.validate()
    progress = progress or (lambda m, f: None)
    if sc.uses_fundamentals() and fund_data is None:
        raise ValueError("These rules need company results")
    progress("Computing the metrics", 0.35)
    frames = metric_frames(closes, values, bench, fund_data)
    today = closes.index[-1]
    snap = snapshot(frames, today, pit.members(closes, values, today, pit_n) if pit_n else None)
    matches = apply(snap, sc)
    top = matches.head(int(sc.top))
    per = capital / max(len(top), 1)
    rows = [{"ticker": t, "industry": (sectors or {}).get(t, ""), **{k: (None if pd.isna(v) else float(v)) for k, v in row.items()},
             "shares": int(per // row["price"]) if row["price"] > 0 else 0} for t, row in top.iterrows()]
    picks = [r["ticker"] for r in rows]
    if picks and (fund_data is not None or cache_dir is not None):
        data = fund_data if fund_data is not None else F.fetch_many(picks, cache_dir, offline)
        pf = F.frames(closes[picks], {t: data.get(t) or {} for t in picks}) if fund_data is None else None
        for r in rows:
            t = r["ticker"]
            if pf is not None:
                r.update({k: F.finite(pf[k][t].iloc[-1]) for k in F.METRICS})
            r["pledge"] = F.pledge(t, data.get(t), cache_dir, offline) if cache_dir is not None else None
    bt_note = None if backtest_too else "Not run"
    try:
        if not backtest_too:
            raise LookupError
        bt = backtest(closes, frames, bench, sc, progress, values, capital, per_order, pit_n)
        daily = bt.pop("daily")
        bt["sip"] = S.report(daily["screen"], daily["nifty"])
    except ValueError as exc:
        bt, bt_note = None, str(exc)
    except LookupError:
        bt = None
    progress("Done", 1.0)
    return {"as_of": str(today.date()), "universe_size": int(len(snap)), "matches": int(len(matches)), "picks": rows,
            "screen": sc.__dict__, "capital": capital, "per_stock": per, "backtest": bt, "backtest_note": bt_note,
            "benchmark": {"symbol": benchmark, "label": A.label(benchmark), "short": A.BENCHMARKS.get(benchmark, {}).get("label", benchmark)},
            "metrics_info": {k: {"label": v[0], "kind": v[1]} for k, v in METRICS.items()}}
