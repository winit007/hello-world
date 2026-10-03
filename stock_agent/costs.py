"""What a delivery trade on NSE really costs: statutory charges plus the price lost to the spread and to impact.

  charges        STT 0.1% on buy and sell, NSE transaction charge, SEBI fee, stamp duty 0.015% on buys, GST on
                 brokerage and exchange charges (the same rates as Paper trading), the depository (DP) charge of
                 about ₹15.93 for every stock sold, and brokerage if your broker charges it for delivery.
  spread         half the bid-ask gap, estimated from how much the stock trades: about 0.03% for a stock that
                 trades ₹1,000 crore a day, 0.1% at ₹100 crore, 0.3% at ₹10 crore (at most 0.5%).
  impact         the price moving against a large order, by the square-root rule used by trading desks:
                 0.5 x daily volatility x sqrt(order / daily traded value). Tiny for small accounts.

The DP charge is a fixed amount, so a small account pays relatively more: selling ₹8,000 of a stock costs
₹15.93 + 0.1% STT, about 0.3%. That is why rebalancing a small account monthly is expensive.
"""
from __future__ import annotations

import math

import pandas as pd

from .paper import charges

DP_CHARGE = 15.93          # CDSL ₹3.50 + a typical broker's ₹10 + 18% GST, per stock per day of selling


def brokerage(value: float, per_order: float) -> float:
    """Delivery brokerage: 0 at Zerodha; others charge up to ₹20 an order (never more than 0.1% here)."""
    return min(per_order, value * 0.001) if per_order > 0 else 0.0


def spread_rate(adv: float) -> float:
    if not adv or not math.isfinite(adv) or adv <= 0:
        return 0.005
    return min(max(0.0003 * math.sqrt(1e10 / adv), 0.0002), 0.005)


def impact_rate(value: float, adv: float, daily_vol: float) -> float:
    if not adv or adv <= 0 or not math.isfinite(adv):
        return 0.02
    vol = daily_vol if daily_vol and math.isfinite(daily_vol) else 0.02
    return min(0.5 * vol * math.sqrt(value / adv), 0.05)


def trade_cost(value: float, side: str, adv: float, daily_vol: float, per_order: float = 0.0) -> dict:
    """Rupee cost of buying or selling `value` rupees of one stock."""
    if value <= 0:
        return {"charges": 0.0, "spread_impact": 0.0}
    fees = charges("stock", "delivery", side, value) + 1.18 * brokerage(value, per_order)
    if side == "sell":
        fees += DP_CHARGE
    slip = value * (spread_rate(adv) + impact_rate(value, adv, daily_vol))
    return {"charges": fees, "spread_impact": slip}


def rebalance_cost(prev: dict, target: dict, account: float, adv: pd.Series, vol: pd.Series,
                   per_order: float = 0.0) -> dict:
    """Cost of moving an `account`-rupee portfolio from weights `prev` to `target`, as a fraction of the account."""
    out = {"charges": 0.0, "spread_impact": 0.0, "orders": 0}
    if account <= 0:
        return {**out, "fraction": 0.0}
    for t in set(prev) | set(target):
        dw = target.get(t, 0.0) - prev.get(t, 0.0)
        if abs(dw) < 1e-9:
            continue
        c = trade_cost(abs(dw) * account, "buy" if dw > 0 else "sell", float(adv.get(t, float("nan"))),
                       float(vol.get(t, float("nan"))), per_order)
        out["charges"] += c["charges"]
        out["spread_impact"] += c["spread_impact"]
        out["orders"] += 1
    out["fraction"] = (out["charges"] + out["spread_impact"]) / account
    return out


def liquidity(closes: pd.DataFrame, values: pd.DataFrame, date) -> tuple[pd.Series, pd.Series]:
    """Median daily traded value (₹) and daily volatility over the 60 days up to `date`."""
    adv = values.loc[:date].iloc[-60:].median()
    vol = closes.loc[:date].iloc[-61:].pct_change().std()
    return adv, vol


def with_band(prev: dict, target: dict, band: float = 0.2) -> dict:
    """Skip the small trades a fund manager would skip: a stock already held within +-`band` (relative) of its
    target weight keeps its drifted weight; the stocks that do trade share the rest of the money."""
    keep = {t: w for t, w in prev.items() if t in target and abs(w - target[t]) <= band * target[t]}
    move = {t: w for t, w in target.items() if t not in keep}
    left = 1.0 - sum(keep.values())
    if not move:
        s = sum(keep.values())
        return {t: w / s for t, w in keep.items()}
    s = sum(move.values())
    if left <= 0 or s <= 0:
        return dict(target)
    return {**keep, **{t: w * left / s for t, w in move.items()}}
