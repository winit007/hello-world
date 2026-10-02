"""Strategy lab: search trade-rule variants and judge them on years they were not tuned on.

For each variant (stop width, target, holding period, trend filter, index filter):
  1. TRAIN on signals before the split date: keep the patterns whose pooled average option return is
     positive with at least 30 trades. That pattern list is part of the strategy.
  2. TEST on signals after the split date using only those patterns, untouched by the choice.
The best variant is chosen by its TRAIN score only; its TEST numbers are the honest estimate.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import asdict, replace

import numpy as np
import pandas as pd

from .tradetest import Strategy, market_regime, trade_outcomes

GRID = {
    "stops": [(0.75, 2.5), (1.5, 3.0)],
    "target": [1.0, 1.5, 2.0],
    "horizon": [3, 5, 10],
    "trend": ["none", "sma200"],
    "market": [False, True],
}


def summarize(o: pd.DataFrame) -> dict:
    o = o[~o["skipped"]]
    if o.empty:
        return {"trades": 0, "win_rate": None, "avg_ret": None, "profit_factor": None, "worst_streak": 0, "per_year": 0}
    r = o.sort_values("exit")["ret"].to_numpy()
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    streak = worst = 0
    for x in r:
        streak = streak + 1 if x <= 0 else 0
        worst = max(worst, streak)
    years = max((o["exit"].max() - o["signal"].min()).days / 365, 0.25)
    return {"trades": int(len(r)), "win_rate": float((r > 0).mean()), "avg_ret": float(r.mean()),
            "profit_factor": float(gains / losses) if losses else math.inf, "worst_streak": int(worst),
            "per_year": float(len(r) / years)}


def run(prices: dict[str, pd.DataFrame], index_df: pd.DataFrame | None, test_years: float = 3.0,
        grid: dict = GRID, min_trades: int = 30, progress=None) -> pd.DataFrame:
    regime = market_regime(index_df)
    last = max(df.index[-1] for df in prices.values())
    split = last - pd.Timedelta(days=int(test_years * 365))
    combos = list(itertools.product(grid["stops"], grid["target"], grid["horizon"], grid["trend"], grid["market"]))
    rows = []
    for k, ((smin, smax), tgt, hz, trend, mkt) in enumerate(combos):
        st = Strategy(stop_min=smin, stop_max=smax, target=tgt, horizon=hz, trend=trend, market=mkt)
        if progress:
            progress(f"Variant {k + 1}/{len(combos)}: {st.label()}", k / len(combos))
        allo = pd.concat([trade_outcomes(df, t, strategy=st, regime=regime, baseline_step=0).assign(ticker=t)
                          for t, df in prices.items()], ignore_index=True)
        allo = allo[~allo["skipped"]]
        train = allo[allo["exit"] < split]
        test = allo[allo["signal"] >= split]
        g = train.groupby("pattern")["ret"].agg(["mean", "size"])
        keep = tuple(sorted(g[(g["mean"] > 0) & (g["size"] >= min_trades)].index))
        tr, te = summarize(train[train["pattern"].isin(keep)]), summarize(test[test["pattern"].isin(keep)])
        rows.append({"strategy": replace(st, patterns=keep), "label": st.label(), "patterns": len(keep),
                     **{f"train_{a}": b for a, b in tr.items()}, **{f"test_{a}": b for a, b in te.items()}})
    df = pd.DataFrame(rows)
    # train score: average option return, but only for variants that trade often enough to matter
    df["train_score"] = np.where(df["train_trades"] >= 100, df["train_avg_ret"].fillna(-1), -1)
    return df.sort_values("train_score", ascending=False).reset_index(drop=True)


def baseline_current(prices, index_df, test_years: float = 3.0) -> dict:
    """The rules the agent used until now (all patterns, no filters), on the same test window."""
    st = Strategy()
    regime = market_regime(index_df)
    last = max(df.index[-1] for df in prices.values())
    split = last - pd.Timedelta(days=int(test_years * 365))
    allo = pd.concat([trade_outcomes(df, t, strategy=st, regime=regime, baseline_step=0) for t, df in prices.items()])
    return summarize(allo[allo["signal"] >= split])


def validate(prices, index_df, st: Strategy, test_years: float = 3.0) -> dict:
    """Train/test summary of one strategy with ALL its patterns (no pattern picking, nothing tuned)."""
    regime = market_regime(index_df)
    last = max(df.index[-1] for df in prices.values())
    split = last - pd.Timedelta(days=int(test_years * 365))
    o = pd.concat([trade_outcomes(df, t, strategy=st, regime=regime, baseline_step=0) for t, df in prices.items()])
    o = o[~o["skipped"]]
    test = o[o["signal"] >= split]
    out = {"strategy": st.label(), "test_from": str(split.date()), "test_to": str(last.date()),
           "universe": len(prices), **{f"test_{k}": v for k, v in summarize(test).items()},
           **{f"train_{k}": v for k, v in summarize(o[o["exit"] < split]).items()},
           "test_stock_avg_r": float(test["r"].mean()) if len(test) else None}
    return out
