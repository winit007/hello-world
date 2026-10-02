"""Backtest candlestick setups as the trades the agent actually recommends.

For every past signal of every pattern we build the same plan the order card shows and replay it:

  * stop beyond the pattern's extreme, padded by 0.25 ATR and kept 0.75-2.5 ATR from the signal close;
    Target 1 at 1.5x that distance (the single-lot exit the Kite GTT uses)
  * buy the at-the-money monthly option at the next day's open; skip if the stock opens past the stop
  * intraday stop first, then Target 1, otherwise sell at the close `horizon` bars after the signal
  * option values from Black-Scholes at max(20d, 60d) realised volatility x 1.1, as in the order sizing

A signal counts as a win when the option is sold for more than it cost. The same trade started on
ordinary days (every 3rd bar, using that bar's own low/high as the "pattern") gives the baseline: what
these stop/target/time rules win on this stock with no setup at all. A setup has an edge only if it
beats that baseline and its average option return is positive.

Outcomes do not depend on the scan date, so they are computed once per stock and filtered by date when
replaying the past (a signal only counts once its trade had finished by that day).
"""
from __future__ import annotations

import calendar
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .options import bs_price
from .patterns import BULLISH, PATTERNS, detect_all

INDIA_RATE, US_RATE = 0.065, 0.04


@dataclass(frozen=True)
class Strategy:
    """The trade rules. The order card, Kite levels, track record and backtest all read these."""
    stop_min: float = 0.75      # stop distance limits, in ATRs from the signal close
    stop_max: float = 2.5
    pad: float = 0.25           # extra ATRs beyond the pattern's extreme
    target: float = 1.5         # Target 1 = target x stop distance (Target 2 = target + 1)
    horizon: int = 5            # time exit, trading days after the signal
    trend: str = "none"         # "none" or "sma200": CALLs only above the 200-day average, PUTs only below
    market: bool = False        # True: CALLs only when the index is above its 50-day average, PUTs below
    patterns: tuple = ()        # allowed patterns; empty = all

    def label(self) -> str:
        return (f"stop {self.stop_min:g}-{self.stop_max:g} ATR, target {self.target:g}R, {self.horizon}d"
                f"{', with 200d trend' if self.trend == 'sma200' else ''}{', index filter' if self.market else ''}")


STRATEGY_FILE = Path(__file__).with_name("strategy.json")
USER_STRATEGY = Path.home() / ".stock_agent" / "strategy.json"


def load_strategy() -> Strategy:
    for path in (USER_STRATEGY, STRATEGY_FILE):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            d["patterns"] = tuple(d.get("patterns", ()))
            return Strategy(**{k: v for k, v in d.items() if k in Strategy.__dataclass_fields__})
        except Exception:
            continue
    return Strategy()


def save_strategy(st: Strategy, path: Path = STRATEGY_FILE) -> None:
    d = asdict(st)
    d["patterns"] = list(st.patterns)
    path.write_text(json.dumps(d, indent=1), encoding="utf-8")


VALIDATION_FILE = Path(__file__).with_name("validation.json")
USER_VALIDATION = Path.home() / ".stock_agent" / "validation.json"


def load_validation() -> dict | None:
    """Latest out-of-sample check of the strategy (your own `lab` run wins over the bundled one)."""
    for path in (USER_VALIDATION, VALIDATION_FILE):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def levels(entry: float, lo: float, hi: float, atr_: float, bull: bool, st: Strategy) -> tuple[float, float, float, float]:
    """(stop, target1, target2, distance) for a signal; shared by the order sizing and the backtest."""
    if bull:
        dist = min(max(entry - (lo - st.pad * atr_), st.stop_min * atr_), st.stop_max * atr_)
        return entry - dist, entry + st.target * dist, entry + (st.target + 1) * dist, dist
    dist = min(max((hi + st.pad * atr_) - entry, st.stop_min * atr_), st.stop_max * atr_)
    return entry + dist, entry - st.target * dist, entry - (st.target + 1) * dist, dist


def market_regime(index_df: pd.DataFrame | None) -> pd.Series | None:
    """True on days the index closed above its 50-day average."""
    if index_df is None or index_df.empty:
        return None
    c = index_df["Close"]
    return (c > c.rolling(50).mean())


def _monthly_expiry(d: date, min_days: int, india: bool) -> date:
    m = d
    for _ in range(4):
        if india:  # NSE: last Tuesday
            last = date(m.year, m.month, calendar.monthrange(m.year, m.month)[1])
            exp = last - timedelta(days=(last.weekday() - 1) % 7)
        else:      # US: third Friday
            first = date(m.year, m.month, 1)
            exp = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
        if (exp - d).days >= min_days:
            return exp
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return d + timedelta(days=60)


def _strike(price: float) -> float:
    for limit, step in ((100, 1), (250, 2.5), (500, 5), (1500, 10), (2500, 20), (5000, 50), (10000, 100)):
        if price < limit:
            return round(price / step) * step
    return round(price / 250) * 250


class _Sim:
    """Arrays + one trade simulation. Kept free of pandas inside the loop for speed."""

    def __init__(self, df: pd.DataFrame, st: Strategy, india: bool):
        horizon = st.horizon
        self.st = st
        self.o, self.h, self.l, self.c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
        self.dates = [ts.date() for ts in df.index]
        tr = np.maximum.reduce([self.h - self.l, np.abs(self.h - np.roll(self.c, 1)), np.abs(self.l - np.roll(self.c, 1))])
        tr[0] = self.h[0] - self.l[0]
        self.atr = pd.Series(tr).rolling(14, min_periods=14).mean().to_numpy()
        lr = np.log(df["Close"]).diff()
        hv = np.maximum(lr.rolling(20).std().to_numpy(), lr.rolling(60).std().to_numpy()) * math.sqrt(252)
        self.sigma = np.where(np.isnan(hv), 0.3, hv * 1.1)
        self.horizon, self.india = horizon, india
        self.r = INDIA_RATE if india else US_RATE
        self.min_days = math.ceil(horizon * 7 / 5) + 3

    def path(self, i: int, bull: bool, candles: int) -> dict | None:
        """When and where the planned trade exits: entry bar, exit bar, exit stock price."""
        n = len(self.c)
        if i + 1 >= n or np.isnan(self.atr[i]):
            return None
        a, entry_ref = self.atr[i], self.c[i]
        lo, hi = self.l[i - candles + 1: i + 1].min(), self.h[i - candles + 1: i + 1].max()
        stop, t1, _, dist = levels(entry_ref, lo, hi, a, bull, self.st)
        if not dist > 0 or not entry_ref > 0:  # flat or broken price history: no meaningful trade
            return None
        j = i + 1
        base = {"i": i, "j": j, "bull": bull, "dist": dist, "ref": entry_ref, "spot_in": self.o[j]}
        if (self.o[j] <= stop) if bull else (self.o[j] >= stop):
            return {**base, "t": j, "s_exit": self.o[j], "skipped": True, "why": "skip"}
        last = min(i + self.horizon, n - 1)
        for t in range(j, last + 1):
            o, h, l = self.o[t], self.h[t], self.l[t]
            if (l <= stop) if bull else (h >= stop):
                return {**base, "t": t, "s_exit": min(o, stop) if bull else max(o, stop), "skipped": False, "why": "stop"}
            if (h >= t1) if bull else (l <= t1):
                return {**base, "t": t, "s_exit": max(o, t1) if bull else min(o, t1), "skipped": False, "why": "target"}
        if i + self.horizon > n - 1:
            return None  # time exit lies beyond the data and nothing triggered yet
        return {**base, "t": last, "s_exit": self.c[last], "skipped": False, "why": "time"}

    def value(self, p: dict, strike: float, kind: str, at_exit: bool) -> float:
        """Black-Scholes value per share of one option on the trade's entry or exit."""
        expiry = _monthly_expiry(self.dates[p["i"]], self.min_days, self.india)
        d, spot = (self.dates[p["t"]], p["s_exit"]) if at_exit else (self.dates[p["j"]], p["spot_in"])
        return max(bs_price(spot, strike, max((expiry - d).days, 0.5) / 365, self.sigma[p["i"]], kind, self.r), 0.05)

    def trade(self, i: int, bull: bool, candles: int, instrument: str = "option", cost: float = 0.0):
        """Return (exit_index, return, r_multiple, skipped) for a signal on bar i, or None.

        instrument "option": return on the option premium. "spot": return on the price itself (coins,
        commodities, shares), net of `cost` (round trip, as a fraction of the position).
        """
        p = self.path(i, bull, candles)
        if p is None:
            return None
        if p["skipped"]:
            return (p["j"], 0.0, 0.0, True)
        move = (p["s_exit"] - p["spot_in"]) if bull else (p["spot_in"] - p["s_exit"])
        if instrument == "spot":
            return (p["t"], move / p["spot_in"] - cost, move / p["dist"], False)
        kind, k = ("call" if bull else "put"), _strike(p["ref"])
        entry, exit_ = self.value(p, k, kind, False), self.value(p, k, kind, True)
        return (p["t"], exit_ / entry - 1 - cost, move / p["dist"], False)


def allowed_mask(df: pd.DataFrame, bull: bool, st: Strategy, regime: pd.Series | None) -> np.ndarray:
    """Bars on which the strategy's trend and index filters allow a trade in this direction."""
    ok = np.ones(len(df), dtype=bool)
    if st.trend == "sma200":
        c = df["Close"]
        above = (c > c.rolling(200, min_periods=150).mean()).to_numpy()
        ok &= above if bull else ~above
    if st.market and regime is not None:
        r = regime.reindex(df.index, method="ffill").fillna(False).to_numpy(bool)
        ok &= r if bull else ~r
    return ok


def trade_outcomes(df: pd.DataFrame, ticker: str, horizon: int | None = None, use_context: bool = True,
                   baseline_step: int = 3, strategy: Strategy | None = None,
                   regime: pd.Series | None = None, instrument: str = "option", cost: float = 0.0,
                   directions: tuple = ("bullish", "bearish")) -> pd.DataFrame:
    """One row per signal (and per baseline trade) with its exit date and option return."""
    st = strategy or load_strategy()
    if horizon and horizon != st.horizon:
        st = Strategy(**{**asdict(st), "horizon": horizon})
    india = ticker.upper().endswith((".NS", ".BO"))
    sim = _Sim(df, st, india)
    sig = detect_all(df, PATTERNS, use_context=use_context)
    allow = {True: allowed_mask(df, True, st, regime), False: allowed_mask(df, False, st, regime)}
    rows = []
    for pat in PATTERNS:
        if st.patterns and pat.name not in st.patterns:
            continue
        if pat.direction not in directions:
            continue
        bull = pat.direction == BULLISH
        for i in np.flatnonzero(sig[pat.name].to_numpy() & allow[bull]):
            res = sim.trade(int(i), bull, pat.candles, instrument, cost)
            if res:
                rows.append((pat.name, pat.direction, sim.dates[i], sim.dates[res[0]], res[1], res[2], res[3]))
    for i in range(60, len(df) - 1, baseline_step) if baseline_step else ():
        for bull, name in ((True, "_baseline_bullish"), (False, "_baseline_bearish")):
            if not allow[bull][i] or (BULLISH if bull else "bearish") not in directions:
                continue
            res = sim.trade(i, bull, 1, instrument, cost)
            if res:
                rows.append((name, BULLISH if bull else "bearish", sim.dates[i], sim.dates[res[0]], res[1], res[2], res[3]))
    out = pd.DataFrame(rows, columns=["pattern", "direction", "signal", "exit", "ret", "r", "skipped"])
    out["signal"] = pd.to_datetime(out["signal"])
    out["exit"] = pd.to_datetime(out["exit"])
    return out


def trade_stats(outcomes: pd.DataFrame, as_of=None, since=None) -> pd.DataFrame:
    """Per pattern: trades, wins, win rate, average option return; plus the baseline for its direction."""
    o = outcomes
    if as_of is not None:
        o = o[o["exit"] <= pd.Timestamp(as_of)]
    if since is not None:
        o = o[o["signal"] >= pd.Timestamp(since)]
    o = o[~o["skipped"]]
    if o.empty:
        return pd.DataFrame(columns=["pattern", "direction", "n", "wins", "win_rate", "avg_ret", "sum_ret",
                                     "avg_r", "baseline", "baseline_ret", "edge"])
    g = o.assign(win=o["ret"] > 0).groupby(["pattern", "direction"]).agg(
        n=("ret", "size"), wins=("win", "sum"), sum_ret=("ret", "sum"), avg_ret=("ret", "mean"), avg_r=("r", "mean")
    ).reset_index()
    g["win_rate"] = g["wins"] / g["n"]
    base = g[g["pattern"].str.startswith("_baseline")].set_index("direction")
    g = g[~g["pattern"].str.startswith("_baseline")].copy()
    g["baseline"] = g["direction"].map(base["win_rate"]) if not base.empty else np.nan
    g["baseline_ret"] = g["direction"].map(base["avg_ret"]) if not base.empty else np.nan
    g["edge"] = g["win_rate"] - g["baseline"]
    return g.reset_index(drop=True)


def pooled_stats(per_ticker: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Universe-wide win rate and average return per pattern (weights = trades)."""
    if not per_ticker:
        return pd.DataFrame()
    allr = pd.concat(per_ticker.values(), ignore_index=True)
    g = allr.groupby(["pattern", "direction"]).agg(n=("n", "sum"), wins=("wins", "sum"), sum_ret=("sum_ret", "sum")).reset_index()
    g["win_rate"] = g["wins"] / g["n"]
    g["avg_ret"] = g["sum_ret"] / g["n"]
    return g.set_index("pattern")
