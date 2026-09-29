"""Turn the outlook into an options trade: BUY CALL, BUY PUT or NO TRADE.

Picks an expiry that outlives the holding period, an at-the-money contract from the live chain
(cached for offline use), and then checks the trade against history: how often did price move
past the breakeven after the same candlestick setup, and what was the average P&L per contract?
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .data import DEFAULT_CACHE
from .patterns import PATTERN_BY_NAME, detect_all

RISK_FREE = 0.04
CONTRACT_SIZE = 100


# --------------------------------------------------------------------------- pricing helpers
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot: float, strike: float, t_years: float, sigma: float, kind: str, r: float = RISK_FREE) -> float:
    if t_years <= 0 or sigma <= 0:
        intrinsic = spot - strike if kind == "call" else strike - spot
        return max(intrinsic, 0.0)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma**2) * t_years) / (sigma * math.sqrt(t_years))
    d2 = d1 - sigma * math.sqrt(t_years)
    if kind == "call":
        return spot * _norm_cdf(d1) - strike * math.exp(-r * t_years) * _norm_cdf(d2)
    return strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def implied_vol(price: float, spot: float, strike: float, t_years: float, kind: str) -> float | None:
    """Bisection on Black-Scholes; returns None when the price is below intrinsic or absurd."""
    lo, hi = 0.01, 5.0
    if price <= bs_price(spot, strike, t_years, 0.0, kind) or price >= bs_price(spot, strike, t_years, hi, kind):
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if bs_price(spot, strike, t_years, mid, kind) > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def historical_vol(close: pd.Series, window: int = 20) -> float:
    lr = np.log(close).diff().dropna().tail(window)
    return float(lr.std(ddof=1) * math.sqrt(252)) if len(lr) > 2 else float("nan")


# --------------------------------------------------------------------------- chain access
def _cache_path(cache_dir: Path, ticker: str) -> Path:
    return cache_dir / "options" / f"{ticker.upper()}.json"


def load_chain(ticker: str, offline: bool = False, cache_dir: Path = DEFAULT_CACHE, max_expiries: int = 10) -> dict:
    """{"spot": float, "expiries": {expiry: {"calls": [...], "puts": [...]}}} or {} if unavailable."""
    path = _cache_path(cache_dir, ticker)
    if not offline:
        try:
            import yfinance as yf

            tk = yf.Ticker(ticker)
            expiries = list(tk.options)[:max_expiries]
            out = {"expiries": {}}
            keep = ["strike", "lastPrice", "bid", "ask", "impliedVolatility", "openInterest", "volume"]
            for e in expiries:
                ch = tk.option_chain(e)
                out["expiries"][e] = {
                    "calls": ch.calls[keep].fillna(0).to_dict("records"),
                    "puts": ch.puts[keep].fillna(0).to_dict("records"),
                }
            if out["expiries"]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(out))
                return out
        except Exception as exc:  # no options on this listing, or network trouble
            print(f"[options] chain fetch failed for {ticker} ({exc}); trying cache")
    if path.exists():
        return json.loads(path.read_text())
    return {}


def choose_expiry(expiries: list[str], as_of: date, horizon_bars: int) -> str | None:
    """First expiry that still has time value left after the holding period ends."""
    need = as_of + timedelta(days=math.ceil(horizon_bars * 7 / 5) + 3)
    for e in sorted(expiries):
        if date.fromisoformat(e) >= need:
            return e
    return None


def premium_of(row: dict) -> float:
    bid, ask, last = float(row.get("bid", 0)), float(row.get("ask", 0)), float(row.get("lastPrice", 0))
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    return last


def choose_contract(rows: list[dict], target: float) -> dict | None:
    live = [r for r in rows if premium_of(r) > 0]
    if not live:
        return None
    return min(live, key=lambda r: abs(float(r["strike"]) - target))


# --------------------------------------------------------------------------- the trade
@dataclass
class OptionTrade:
    ticker: str
    action: str                       # "BUY CALL" / "BUY PUT" / "NO TRADE"
    reason: str
    spot: float
    horizon: int
    expiry: str | None = None
    strike: float | None = None
    premium: float | None = None      # per share
    breakeven: float | None = None
    required_move: float | None = None
    prob_profit: float | None = None  # historical P(move beyond breakeven) after the same setup
    prob_basis: str = ""              # which history the probability came from
    exp_pnl_contract: float | None = None
    iv: float | None = None
    hv: float | None = None
    implied_move: float | None = None
    sample: int = 0
    warnings: list[str] = field(default_factory=list)

    def one_liner(self) -> str:
        if self.action == "NO TRADE":
            return f"{self.ticker}: NO TRADE — {self.reason}"
        s = (f"{self.ticker}: {self.action} {self.strike:g} exp {self.expiry} @ {self.premium:.2f} "
             f"(spot {self.spot:.2f}, breakeven {self.breakeven:.2f}, needs {self.required_move:+.1%})")
        if self.prob_profit is not None:
            s += f" · hist. P(profit) {self.prob_profit:.0%} · avg P&L/contract {self.exp_pnl_contract:+.0f}"
        return s


def forward_returns_for(df: pd.DataFrame, horizon: int, pattern_names: list[str]) -> tuple[np.ndarray, str]:
    """Forward returns after the given patterns fired (conditional) or after every bar (unconditional)."""
    close = df["Close"].to_numpy()
    if pattern_names:
        sig = detect_all(df, [PATTERN_BY_NAME[n] for n in pattern_names]).any(axis=1).to_numpy()
        idx = np.flatnonzero(sig)
        idx = idx[idx + horizon < len(close)]
        if len(idx) >= 10:
            return close[idx + horizon] / close[idx] - 1, "after " + " / ".join(pattern_names)
    r = close[horizon:] / close[:-horizon] - 1
    return r, "all bars (no counted pattern)"


def recommend(outlook, df: pd.DataFrame, chain: dict, min_confidence: float = 0.15) -> OptionTrade:
    spot = outlook.last_close
    h = outlook.horizon
    trade = OptionTrade(outlook.ticker, "NO TRADE", "", spot, h, hv=historical_vol(df["Close"]))

    if outlook.bias == "neutral" or outlook.confidence < min_confidence:
        trade.reason = (f"bias {outlook.bias} with {outlook.confidence:.0%} confidence is below the "
                        f"{min_confidence:.0%} threshold; buying premium here is a coin flip minus theta")
        return trade

    kind = "call" if outlook.bias == "bullish" else "put"
    trade.action = "BUY CALL" if kind == "call" else "BUY PUT"
    counted = [s["pattern"] for s in outlook.signals if s.get("counted")]
    trade.reason = f"{outlook.bias} bias ({outlook.confidence:.0%}): " + "; ".join(outlook.reasons[:3])

    rets, basis = forward_returns_for(df, h, counted)
    trade.prob_basis = basis
    trade.sample = len(rets)

    expiries = list(chain.get("expiries", {}).keys())
    expiry = choose_expiry(expiries, outlook.as_of.date(), h) if expiries else None
    if expiry is None:
        trade.warnings.append("no option chain available; direction only, pick an expiry at least "
                              f"{math.ceil(h * 7 / 5) + 3} days out yourself")
        trade.prob_profit = float((rets > 0).mean()) if kind == "call" else float((rets < 0).mean())
        return trade

    rows = chain["expiries"][expiry]["calls" if kind == "call" else "puts"]
    row = choose_contract(rows, spot)
    if row is None:
        trade.warnings.append(f"no priced {kind}s for {expiry}; direction only")
        return trade

    strike, prem = float(row["strike"]), premium_of(row)
    trade.expiry, trade.strike, trade.premium = expiry, strike, prem
    trade.breakeven = strike + prem if kind == "call" else strike - prem
    trade.required_move = trade.breakeven / spot - 1
    if float(row.get("bid", 0)) <= 0:
        trade.warnings.append("bid/ask unavailable (market closed?); premium is the last trade, re-check live quotes")

    # historical check: did price get past the breakeven within the holding period?
    if kind == "call":
        trade.prob_profit = float((rets > trade.required_move).mean())
        payoff = np.maximum(spot * (1 + rets) - strike, 0.0)
    else:
        trade.prob_profit = float((rets < trade.required_move).mean())
        payoff = np.maximum(strike - spot * (1 + rets), 0.0)
    # payoff at the horizon, not at expiry: the leftover time value is treated as zero (conservative)
    trade.exp_pnl_contract = float((payoff.mean() - prem) * CONTRACT_SIZE)

    dte = (date.fromisoformat(expiry) - outlook.as_of.date()).days
    t_years = max(dte, 1) / 365
    iv = float(row.get("impliedVolatility", 0) or 0)
    if iv < 0.02:  # feed returns ~0 outside market hours; solve it from the price instead
        iv = implied_vol(prem, spot, strike, t_years, kind) or float("nan")
    trade.iv = iv
    if not math.isnan(iv):
        trade.implied_move = iv * math.sqrt(t_years)
        if not math.isnan(trade.hv) and iv > 1.4 * trade.hv:
            trade.warnings.append(f"implied vol {iv:.0%} is well above realised {trade.hv:.0%}: premium is rich, "
                                  "consider a debit spread instead of a naked long option")
    if trade.prob_profit is not None and trade.prob_profit < 0.4:
        trade.warnings.append(f"price got past the breakeven only {trade.prob_profit:.0%} of the time historically")
    if trade.exp_pnl_contract is not None and trade.exp_pnl_contract < 0:
        # direction may be right, but the premium eats the expected move: do not buy it
        trade.action = "NO TRADE"
        trade.reason = (f"{outlook.bias} bias, but the ATM {kind} needs {trade.required_move:+.1%} in {h} bars; "
                        f"history cleared that only {trade.prob_profit:.0%} of the time ({trade.prob_basis}) for an "
                        f"average {trade.exp_pnl_contract:+.0f} per contract. Wait for a cheaper entry or use a spread")
    return trade


def render_markdown(trade: OptionTrade) -> str:
    md = ["## Options trade", "", f"**{trade.one_liner()}**", ""]
    if trade.expiry:
        md += ["| | |", "|---|---|",
               f"| Contract evaluated | {'BUY CALL' if trade.breakeven > trade.strike else 'BUY PUT'} {trade.strike:g} expiring {trade.expiry} |",
               f"| Premium (per share / per contract) | {trade.premium:.2f} / {trade.premium * CONTRACT_SIZE:.0f} |",
               f"| Breakeven at expiry | {trade.breakeven:.2f} ({trade.required_move:+.2%} from spot) |",
               f"| Historical P(beyond breakeven in {trade.horizon} bars) | {trade.prob_profit:.0%} ({trade.prob_basis}, n={trade.sample}) |",
               f"| Historical avg P&L per contract at day {trade.horizon} | {trade.exp_pnl_contract:+.0f} |",
               f"| Implied vol / 20d realised vol | {trade.iv:.0%} / {trade.hv:.0%} |" if trade.iv is not None and not math.isnan(trade.iv) else f"| 20d realised vol | {trade.hv:.0%} |",
               f"| Implied move to expiry (1σ) | ±{trade.implied_move:.1%} |" if trade.implied_move else "| Implied move | n/a |",
               ""]
    elif trade.action != "NO TRADE":
        md += [f"Direction only. Historical P(move in your favour over {trade.horizon} bars): {trade.prob_profit:.0%} "
               f"({trade.prob_basis}, n={trade.sample}).", ""]
    md.append(f"Reason: {trade.reason}")
    if trade.warnings:
        md += ["", "Warnings:", ""] + [f"- {w}" for w in trade.warnings]
    md += ["", "_Options lose value every day you hold them. Risk only the premium you can afford to lose entirely._"]
    return "\n".join(md)
