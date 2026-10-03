"""Fairer stock lists: the biggest NSE stocks by trading value as they were on each date, not as they are today.

Testing on today's Nifty 200 quietly picks companies that we already know survived and grew (survivorship bias):
a stock that collapsed in 2019 is not in today's list, so the backtest never owns it. Here the list is rebuilt on
every date from all NSE stocks: the N with the highest median daily traded value over the previous 60 days,
among those with a year of prices. A company that was big then and fell later is in; a small company that grew
big later only joins once it actually traded that much.

Still not perfect: companies that have since delisted (mergers, insolvency) have no prices on Yahoo, so they
cannot be held. The bias is much smaller, not gone.
"""
from __future__ import annotations

import pandas as pd

PIT = {"nse_top100": 100, "nse_top200": 200, "nse_top500": 500}
LABELS = {"nse_top100": "Top 100 NSE stocks at each date", "nse_top200": "Top 200 NSE stocks at each date",
          "nse_top500": "Top 500 NSE stocks at each date"}


def size(universe: str) -> int | None:
    return PIT.get(universe)


def _ranked(closes: pd.DataFrame, values: pd.DataFrame, dates) -> pd.DataFrame:
    """Median 60-day traded value on each date, NaN for stocks without a year of prices by then."""
    med = values.rolling(60, min_periods=40).median()
    hist = closes.notna().rolling(260, min_periods=1).sum() >= 240
    return med.where(hist).reindex(dates)


def candidates(closes: pd.DataFrame, values: pd.DataFrame, n: int) -> list[str]:
    """Every stock that was among the top `n` on at least one month end (keeps the data small)."""
    ends = pd.Series(closes.index, index=closes.index).groupby(closes.index.to_period("M")).max()
    r = _ranked(closes, values, pd.DatetimeIndex(ends.values)).rank(axis=1, ascending=False)
    return sorted(r.columns[(r <= n).any()])


def members(closes: pd.DataFrame, values: pd.DataFrame, date, n: int) -> pd.Index:
    """The top `n` stocks by traded value on `date`, using data up to that day only."""
    c, v = closes.loc[:date].iloc[-260:], values.loc[:date].iloc[-60:]
    ok = c.notna().sum() >= 240
    med = v.median().where(ok).dropna()
    return med.sort_values(ascending=False).index[:n]
