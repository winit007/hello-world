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


def reverse_study(prices: dict[str, pd.DataFrame], test_years: float = 3.0, cost: float = 0.02,
                  margin_rate: float = 0.18) -> pd.DataFrame:
    """Compare the strategy with three ways of reversing it, on the same signals and the same exits.

    buy        what the agent recommends: buy the at-the-money option
    flip       buy the opposite option instead (PUT where it said CALL), with its own mirrored stop/target
    sell       sell the recommended option (collect the premium); exits on the same days
    spread     sell the recommended option and buy one ~1.5 ATR further out, capping the loss

    `cost` is a round-trip charge per option leg as a share of its premium (brokerage, taxes, bid/ask).
    Returns are on the premium paid (buy, flip), on the premium received (sell) and on the maximum
    possible loss (spread). `on_margin` is the sell trade's return on an approximate exchange margin.
    """
    from .patterns import BULLISH, PATTERNS, detect_all
    from .tradetest import _Sim, _strike, load_strategy

    st = load_strategy()
    last = max(df.index[-1] for df in prices.values())
    split = last - pd.Timedelta(days=int(test_years * 365))
    rows = []
    for t, df in prices.items():
        sim = _Sim(df, st, t.upper().endswith((".NS", ".BO")))
        sig = detect_all(df, PATTERNS)
        for pat in PATTERNS:
            bull = pat.direction == BULLISH
            for i in np.flatnonzero(sig[pat.name].to_numpy()):
                i = int(i)
                p = sim.path(i, bull, pat.candles)
                if p is None or p["skipped"]:
                    continue
                kind = "call" if bull else "put"
                k1 = _strike(p["ref"])
                e1, x1 = sim.value(p, k1, kind, False), sim.value(p, k1, kind, True)
                k2 = _strike(p["ref"] + (1.5 if bull else -1.5) * sim.atr[i])
                if k2 == k1:
                    k2 = k1 + (1 if bull else -1) * max(abs(k1) * 0.01, 1)
                e2, x2 = sim.value(p, k2, kind, False), sim.value(p, k2, kind, True)
                width = abs(k2 - k1)
                credit = e1 - e2
                max_loss = max(width - credit, 0.05)
                row = {"ticker": t, "pattern": pat.name, "signal": df.index[i], "test": df.index[i] >= split,
                       "buy": x1 / e1 - 1 - cost,
                       "sell": (e1 - x1) / e1 - cost,
                       "on_margin": ((e1 - x1) - cost * e1) / (margin_rate * p["spot_in"]),
                       "spread": ((credit - (x1 - x2)) - cost * (e1 + e2)) / max_loss}
                fp = sim.path(i, not bull, pat.candles)  # flipped direction: its own stop and target
                if fp is not None and not fp["skipped"]:
                    fk, fkind = _strike(fp["ref"]), ("put" if bull else "call")
                    fe, fx = sim.value(fp, fk, fkind, False), sim.value(fp, fk, fkind, True)
                    row["flip"] = fx / fe - 1 - cost
                rows.append(row)
    return pd.DataFrame(rows)


def summarize_reverse(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for period, part in (("older years", df[~df["test"]]), ("last 3 years", df[df["test"]])):
        for col, label in (("buy", "Buy the option (current)"), ("flip", "Flip: buy the opposite option"),
                           ("sell", "Sell the recommended option"), ("spread", "Credit spread (capped loss)")):
            r = part[col].dropna().to_numpy()
            if not len(r):
                continue
            gains, losses = r[r > 0].sum(), -r[r < 0].sum()
            out.append({"period": period, "strategy": label, "trades": len(r), "win_rate": (r > 0).mean(),
                        "avg": r.mean(), "worst": r.min(), "profit_factor": gains / losses if losses else np.inf,
                        "on_margin": part["on_margin"].mean() if col == "sell" else np.nan})
    return pd.DataFrame(out)
