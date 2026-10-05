"""Benchmarks to compare against, and where a strategy's extra return came from.

Index funds (the "BEES" ETFs) include dividends. Yahoo's index levels do not, so for those a typical dividend
yield is added back day by day (the `yield` below); the label says so.

The extra return over the benchmark, in % a year, is split in three parts that add up exactly:
  universe    owning every stock of the chosen list in equal money, against the benchmark
              (a list of today's index members also carries survivorship bias)
  selection   your rules' picks, before costs, against every stock of the list
  costs       what trading costs took away
Then a regression of weekly returns on the benchmark: beta (how much of the market's move it carries),
alpha (yearly return not explained by beta), tracking error, information ratio, and up / down capture.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .data import MONTH_END

BENCHMARKS = {
    "NIFTYBEES.NS": {"label": "Nifty 50", "yield": 0.0},
    "JUNIORBEES.NS": {"label": "Nifty Next 50", "yield": 0.0},
    "^CNX200": {"label": "Nifty 200", "yield": 0.013},
    "^CRSLDX": {"label": "Nifty 500", "yield": 0.012},
    "^NSEMDCP50": {"label": "Nifty Midcap 50", "yield": 0.008},
    "NIFTYSMLCAP250.NS": {"label": "Nifty Smallcap 250", "yield": 0.007},
    "BANKBEES.NS": {"label": "Nifty Bank", "yield": 0.0},
}
DEFAULT = "NIFTYBEES.NS"


def label(symbol: str) -> str:
    b = BENCHMARKS.get(symbol, {"label": symbol, "yield": 0.0})
    return b["label"] + (f" (dividends added at {b['yield']:.1%} a year)" if b["yield"] else " (index fund)")


def total_return(symbol: str, close: pd.Series) -> pd.Series:
    """Price series with the typical dividend yield added back, for indices quoted without dividends."""
    y = BENCHMARKS.get(symbol, {}).get("yield", 0.0)
    close = close.dropna()
    if not y or close.empty:
        return close
    days = (close.index - close.index[0]).days.to_numpy()
    return close * np.power(1 + y, days / 365.25)


def _cagr(s: pd.Series) -> float:
    years = (s.index[-1] - s.index[0]).days / 365.25
    return float((s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1) if years > 0 and s.iloc[0] > 0 else 0.0


def compare(strategy: pd.Series, gross: pd.Series, equal_weight: pd.Series, bench: pd.Series) -> dict:
    """All four are growth curves over the same dates (daily or weekly)."""
    df = pd.concat({"s": strategy, "g": gross, "e": equal_weight, "b": bench}, axis=1).dropna()
    if len(df) < 30:
        return {}
    c = {k: _cagr(df[k]) for k in df}
    out = {"strategy_cagr": c["s"], "benchmark_cagr": c["b"], "extra": c["s"] - c["b"],
           "universe": c["e"] - c["b"], "selection": c["g"] - c["e"], "costs": c["s"] - c["g"]}
    w = df.resample("W-FRI").last().pct_change().dropna()
    rs, rb = w["s"], w["b"]
    var = float(rb.var())
    beta = float(rs.cov(rb) / var) if var > 0 else float("nan")
    te = float((rs - rb).std() * math.sqrt(52))
    m = df.resample(MONTH_END).last().pct_change().dropna()
    up, dn = m["b"] > 0, m["b"] < 0
    out.update({
        "beta": beta,
        "alpha": float((rs - beta * rb).mean() * 52) if math.isfinite(beta) else None,
        "correlation": float(rs.corr(rb)),
        "tracking_error": te,
        "information_ratio": (c["s"] - c["b"]) / te if te > 0 else None,
        "up_capture": float(m.loc[up, "s"].mean() / m.loc[up, "b"].mean()) if up.sum() >= 3 else None,
        "down_capture": float(m.loc[dn, "s"].mean() / m.loc[dn, "b"].mean()) if dn.sum() >= 3 else None,
        "months_beaten": float((m["s"] > m["b"]).mean()) if len(m) else None,
    })
    return out
