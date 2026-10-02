"""Turn a directional idea into an exact order: contract, lots, cost, stop-loss and targets.

Stop-loss sits just beyond the pattern's extreme (its low for a CALL, its high for a PUT), padded by a
quarter of the 14-day ATR and kept between 0.75 and 2.5 ATR from the entry. Targets are 1.5R and 2.5R.
Option prices at the stop and targets are re-priced with Black-Scholes one trading day later, so they
are estimates of where to place the premium-based exit orders.

Lots = floor(capital × risk% / loss per lot at the stop), also capped so the total premium stays under
`max_alloc` of capital. Zero lots means one lot already risks more than your budget.
"""
from __future__ import annotations

import calendar
import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import lots as L
from .options import bs_price, choose_contract, choose_expiry, historical_vol, implied_vol, premium_of
from .patterns import BULLISH, PATTERN_BY_NAME

INDIA_RATE, US_RATE = 0.065, 0.04


@dataclass
class TradePlan:
    ticker: str
    side: str                     # CALL / PUT
    entry: float                  # underlying reference price (last close)
    expiry: date
    expiry_source: str
    strike: float
    premium: float
    premium_source: str
    lot_size: int | None
    lot_source: str
    lots: int
    cost: float                   # premium × lot × lots
    stop_underlying: float
    stop_premium: float
    target1_underlying: float
    target1_premium: float
    target2_underlying: float
    target2_premium: float
    max_loss_at_stop: float       # for the sized position
    max_loss_total: float         # premium goes to zero
    time_stop: date
    capital: float
    risk_pct: float
    currency: str
    warnings: list[str] = field(default_factory=list)

    @property
    def contract(self) -> str:
        suffix = "CE" if self.side == "CALL" else "PE"
        return f"{self.ticker.split('.')[0]} {self.expiry:%d-%b-%Y} {self.strike:g} {suffix}"

    def lines(self) -> list[str]:
        c = self.currency
        if self.lot_size is None:
            return [f"   Not tradable in F&O: {self.lot_source}"]
        head = (f"   BUY {self.lots} lot{'s' if self.lots != 1 else ''} {self.contract} · lot {self.lot_size} · "
                f"premium ~{c}{self.premium:.2f} · cost {money(self.cost, c)}")
        if self.lots == 0:
            head = (f"   0 lots: 1 lot of {self.contract} (lot {self.lot_size}, ~{c}{self.premium:.2f}) risks "
                    f"{money((self.premium - self.stop_premium) * self.lot_size, c)} to the stop, above your "
                    f"{money(self.capital * self.risk_pct, c)} budget")
        return [
            head,
            f"   STOP: sell if {self.ticker.split('.')[0]} trades {'below' if self.side == 'CALL' else 'above'} "
            f"{self.stop_underlying:.2f} (premium ~{c}{self.stop_premium:.2f})",
            f"   TARGET: book half at {self.target1_underlying:.2f} (~{c}{self.target1_premium:.2f}), rest at "
            f"{self.target2_underlying:.2f} (~{c}{self.target2_premium:.2f}) · time exit {self.time_stop:%d-%b}",
        ]


def money(x: float, currency: str) -> str:
    """₹5,00,000 (Indian grouping) or $25,000."""
    if currency != "₹":
        return f"{currency}{x:,.0f}"
    s = f"{abs(round(x)):d}"
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ("-" if x < 0 else "") + "₹" + ",".join(groups + [tail]) if groups else ("-" if x < 0 else "") + "₹" + tail


def _round_tick(x: float, tick: float = 0.05) -> float:
    return round(round(x / tick) * tick, 2)


def strike_step(price: float) -> float:
    """Typical NSE stock-option strike interval. Approximate; the real chain overrides it."""
    for limit, step in ((100, 1), (250, 2.5), (500, 5), (1500, 10), (2500, 20), (5000, 50), (10000, 100)):
        if price < limit:
            return step
    return 250


def nse_monthly_expiry(as_of: date, min_days: int) -> date:
    """NSE stock options expire on the last Tuesday of the month (since Sep 2025)."""
    d = as_of
    for _ in range(4):
        last = date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
        exp = last - timedelta(days=(last.weekday() - 1) % 7)
        if (exp - as_of).days >= min_days:
            return exp
        d = last + timedelta(days=1)
    raise RuntimeError("no expiry found")


def atr(df: pd.DataFrame, n: int = 14) -> float:
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return float(tr.tail(n).mean())


def add_trading_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def plan_trade(
    ticker: str,
    df: pd.DataFrame,
    direction: str,
    horizon: int,
    pattern: str | None = None,
    signal_date: str | None = None,
    chain: dict | None = None,
    capital: float | None = None,
    risk_pct: float = 0.02,
    max_alloc: float = 0.25,
    lot_override: int | None = None,
    offline: bool = False,
    cache_dir=None,
) -> TradePlan:
    india = ticker.upper().endswith(L.INDIA_SUFFIXES)
    currency = "₹" if india else "$"
    capital = capital or (500_000 if india else 25_000)
    rate = INDIA_RATE if india else US_RATE
    bullish = direction == BULLISH
    kind = "call" if bullish else "put"
    entry = float(df["Close"].iloc[-1])
    as_of = df.index[-1].date()
    warnings: list[str] = []

    # ---- stop and targets on the underlying
    a = atr(df)
    candles = PATTERN_BY_NAME[pattern].candles if pattern else 3
    end = df.index.get_loc(pd.Timestamp(signal_date)) if signal_date else len(df) - 1
    window = df.iloc[max(0, end - candles + 1): end + 1]
    from .tradetest import levels, load_strategy

    stop, t1, t2, dist = levels(entry, float(window["Low"].min()), float(window["High"].max()), a, bullish, load_strategy())

    # ---- contract: real chain when we have one, NSE conventions otherwise
    min_days = math.ceil(horizon * 7 / 5) + 3
    expiry, strike, premium, sigma = None, None, None, None
    if chain and chain.get("expiries"):
        e = choose_expiry(list(chain["expiries"]), as_of, horizon)
        row = choose_contract(chain["expiries"][e]["calls" if bullish else "puts"], entry) if e else None
        if row:
            expiry, strike, premium = date.fromisoformat(e), float(row["strike"]), premium_of(row)
            expiry_source, premium_source = "option chain", "option chain"
            iv = float(row.get("impliedVolatility") or 0)
            t = max((expiry - as_of).days, 1) / 365
            sigma = iv if iv > 0.02 else implied_vol(premium, entry, strike, t, kind)
    if expiry is None:
        expiry = nse_monthly_expiry(as_of, min_days) if india else _next_us_monthly(as_of, min_days)
        expiry_source = "NSE monthly (last Tuesday)" if india else "US monthly (3rd Friday)"
        step = strike_step(entry)
        strike = round(entry / step) * step
        hv = max(historical_vol(df["Close"], 20), historical_vol(df["Close"], 60))
        sigma = hv * 1.1  # implied usually trades above realised
        t = max((expiry - as_of).days, 1) / 365
        premium = _round_tick(bs_price(entry, strike, t, sigma, kind, rate))
        premium_source = f"estimated (Black-Scholes, vol {sigma:.0%}); check the live quote before ordering"
        warnings.append("no live option chain: premium is a model estimate, strike interval approximate")
    sigma = sigma or 0.3

    # ---- option value at stop / targets one trading day later
    t_next = max((expiry - as_of).days - 1, 1) / 365
    price_at = lambda s: _round_tick(bs_price(s, strike, t_next, sigma, kind, rate))
    stop_prem, t1_prem, t2_prem = price_at(stop), price_at(t1), price_at(t2)

    # ---- lots
    if lot_override:
        lot, lot_src = lot_override, "user supplied"
    else:
        lot, lot_src = L.lot_size(ticker, expiry, offline, **({"cache_dir": cache_dir} if cache_dir else {}))
    n_lots, cost, loss_stop, loss_total = 0, 0.0, 0.0, 0.0
    if lot:
        per_lot_risk = max(premium - stop_prem, 0.05) * lot
        n_lots = int((capital * risk_pct) // per_lot_risk)
        n_lots = min(n_lots, int((capital * max_alloc) // (premium * lot)))
        cost = premium * lot * n_lots
        loss_stop = per_lot_risk * n_lots
        loss_total = cost
    if (expiry - as_of).days < 7:
        warnings.append("expiry is less than a week away: time decay is fast")

    return TradePlan(
        ticker, "CALL" if bullish else "PUT", entry, expiry, expiry_source, float(strike), float(premium),
        premium_source, lot, lot_src, n_lots, cost, round(stop, 2), stop_prem, round(t1, 2), t1_prem,
        round(t2, 2), t2_prem, loss_stop, loss_total, add_trading_days(as_of, horizon), capital, risk_pct,
        currency, warnings,
    )


def _next_us_monthly(as_of: date, min_days: int) -> date:
    d = as_of
    for _ in range(4):
        first = date(d.year, d.month, 1)
        third_fri = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
        if (third_fri - as_of).days >= min_days:
            return third_fri
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    raise RuntimeError("no expiry found")
