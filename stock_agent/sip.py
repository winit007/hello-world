"""Investing over time instead of all at once: SIP, STP and lump sum, replayed on a strategy's daily values.

  SIP        the same amount on the first trading day of every month.
  STP        a lump sum parked in a liquid fund (6.5% a year) and moved into the strategy in equal monthly parts
             over 6, 12 or 24 months; whatever is left moves in with the last part.
  lump sum   everything on the first day.
Results are per rupee (SIP: per rupee a month), so the page can scale them to any amount. XIRR is the yearly
return that makes the dated cash flows add up, the way mutual-fund statements report SIP returns. Before tax.
"""
from __future__ import annotations

import pandas as pd

LIQUID = 0.065


def xirr(flows: list[tuple[pd.Timestamp, float]]) -> float | None:
    """Yearly rate r with sum(cf / (1+r)^(years)) = 0; invest = negative, value at the end = positive."""
    if not flows or all(cf >= 0 for _, cf in flows) or all(cf <= 0 for _, cf in flows):
        return None
    t0 = flows[0][0]
    yrs = [((d - t0).days / 365.25, cf) for d, cf in flows]
    f = lambda r: sum(cf / (1 + r) ** y for y, cf in yrs)
    lo, hi = -0.99, 10.0
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def _month_starts(idx: pd.DatetimeIndex) -> list[pd.Timestamp]:
    s = pd.Series(idx, index=idx)
    return list(s.groupby(idx.to_period("M")).min())


def sip(nav: pd.Series) -> dict:
    nav = nav.dropna()
    days = _month_starts(nav.index)
    units = sum(1.0 / float(nav.loc[d]) for d in days)
    value = units * float(nav.iloc[-1])
    flows = [(d, -1.0) for d in days] + [(nav.index[-1], value)]
    return {"invested": float(len(days)), "value": float(value), "xirr": xirr(flows), "months": len(days)}


def stp(nav: pd.Series, months: int, liquid: float = LIQUID) -> dict:
    nav = nav.dropna()
    days = _month_starts(nav.index)[:months]
    cash, units, last = 1.0, 0.0, days[0]
    part = 1.0 / months
    for i, d in enumerate(days):
        cash *= (1 + liquid) ** ((d - last).days / 365.25)
        last = d
        move = cash if i == len(days) - 1 else min(part, cash)
        units += move / float(nav.loc[d])
        cash -= move
    value = units * float(nav.iloc[-1])
    return {"invested": 1.0, "value": float(value), "xirr": xirr([(nav.index[0], -1.0), (nav.index[-1], value)]), "months": months}


def lump(nav: pd.Series) -> dict:
    nav = nav.dropna()
    value = float(nav.iloc[-1] / nav.iloc[0])
    return {"invested": 1.0, "value": value, "xirr": xirr([(nav.index[0], -1.0), (nav.index[-1], value)]), "months": 0}


def rolling_sip(nav: pd.Series, bench: pd.Series, months: int = 36) -> dict | None:
    """XIRR of every `months`-long SIP that fits, started each month: the range of outcomes."""
    nav, bench = nav.dropna(), bench.reindex(nav.index).ffill()
    starts = _month_starts(nav.index)
    out = []
    for k in range(0, len(starts) - months):
        days = starts[k:k + months]
        end = starts[k + months]
        rows = []
        for series in (nav, bench):
            units = sum(1.0 / float(series.loc[d]) for d in days)
            rows.append(xirr([(d, -1.0) for d in days] + [(end, units * float(series.loc[end]))]))
        if None not in rows:
            out.append(rows)
    if len(out) < 3:
        return None
    df = pd.DataFrame(out, columns=["strategy", "benchmark"])
    q = lambda c: {"worst": float(df[c].min()), "median": float(df[c].median()), "best": float(df[c].max())}
    return {"months": months, "windows": len(df), "strategy": q("strategy"), "benchmark": q("benchmark"),
            "beat_share": float((df["strategy"] > df["benchmark"]).mean()),
            "loss_share": float((df["strategy"] < 0).mean())}


def report(strategy: pd.Series, bench: pd.Series) -> dict:
    """Everything the page shows, per rupee, for a strategy and its benchmark over the same days."""
    df = pd.concat({"s": strategy, "b": bench}, axis=1).dropna()
    if len(df) < 300:
        return {}
    s, b = df["s"], df["b"]
    days = _month_starts(df.index)
    curve, units_s, units_b, invested = [], 0.0, 0.0, 0.0
    weekly = set(df.resample("W-FRI").last().index)
    month_set = set(days)
    for d in df.index:
        if d in month_set:
            units_s += 1 / s.loc[d]
            units_b += 1 / b.loc[d]
            invested += 1
        if d in weekly or d == df.index[-1]:
            curve.append({"date": str(d.date()), "invested": invested, "strategy": units_s * s.loc[d], "benchmark": units_b * b.loc[d]})
    return {"start": str(df.index[0].date()), "end": str(df.index[-1].date()),
            "sip": {"strategy": sip(s), "benchmark": sip(b)},
            "lump": {"strategy": lump(s), "benchmark": lump(b)},
            "stp": {str(m): {"strategy": stp(s, m), "benchmark": stp(b, m)} for m in (6, 12, 24) if len(days) > m},
            "rolling": rolling_sip(s, b, 36), "curve": curve}
