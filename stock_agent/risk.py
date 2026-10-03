"""Portfolio risk: everything you hold, in one view.

Positions come from Paper trading, the open trades in My trades (real or practice broker orders and ones you
logged) and My holdings. Each position can be re-priced for any price of its share, coin or commodity: options by
Black-Scholes at the stock's own volatility, so a fall is felt the way the option really moves.

  money at risk at the stops   what you lose if every stop-loss is hit (positions without a stop are listed)
  exposure                     the rupee amount that moves with the market (for options, delta x share value)
  beta-weighted exposure       exposure x each stock's beta: how many rupees of "Nifty" you effectively own
  Nifty -5% / +5%              every position re-priced after its share moves by beta x 5%
  one-day VaR (95%)            the loss exceeded on only 1 day in 20, from re-pricing today's positions with each
                               of the last 500 days' moves (historical simulation); expected shortfall is the
                               average loss on those worst 1-in-20 days
  sectors, correlation         where the money is concentrated and how much the positions move together
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

import numpy as np
import pandas as pd

LIMITS = {"risk_at_stops": 0.06, "single": 0.20, "sector": 0.35, "correlation": 0.6, "beta_exposure": 1.5}


@dataclass
class Position:
    source: str                     # Paper / My trades / Holdings
    label: str
    symbol: str                     # Yahoo symbol of what moves the price
    kind: str                       # stock, option, crypto, commodity, holding
    reprice: Callable[[float, float], float]   # (underlying price, days ahead) -> position value in ₹ (shorts negative)
    stop: float | None = None       # underlying level
    has_stop_concept: bool = True   # long-term holdings are not expected to have stops
    extra: dict = field(default_factory=dict)


def is_etf(symbol: str) -> bool:
    s = symbol.upper().replace(".NS", "").replace(".BO", "")
    return s.endswith(("BEES", "ETF", "IETF")) or s.startswith(("SETF", "MON100", "MAFANG", "MID150", "NIFTY1", "ICICIB22"))


def bs_delta(spot: float, strike: float, years: float, vol: float, kind: str, rate: float = 0.065) -> float:
    if years <= 0 or vol <= 0:
        return (1.0 if spot > strike else 0.0) if kind == "call" else (-1.0 if spot < strike else 0.0)
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2) * years) / (vol * math.sqrt(years))
    n = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
    return n if kind == "call" else n - 1


# ----------------------------------------------------------------------------- building positions
def from_paper(acct) -> list[Position]:
    out = []
    for p in acct.data.get("positions", []):
        sign = -1.0 if p["side"] == "short" else 1.0

        def rp(spot, days, p=p, sign=sign):
            at = datetime.now() + timedelta(days=days)
            return sign * acct._unit_price(p, spot, at) * p["qty"]
        out.append(Position("Paper", acct._label(p), p["symbol"], p["instrument"], rp, p.get("stop"),
                            extra={"id": p["id"], "target": p.get("target")}))
    return out


def from_journal(entries: list[dict], vol_of: Callable[[str], float]) -> list[Position]:
    """Open option trades in My trades (bought calls and puts)."""
    from .options import bs_price

    out = []
    for e in entries:
        if e.get("status") != "open" or not e.get("ticker"):
            continue
        qty = float(e.get("lots") or 0) * float(e.get("lot_size") or 0)
        if qty <= 0:
            continue
        c = (e.get("kite") or {}).get("contract") or {}
        kind = "call" if (e.get("side") or "CALL").upper() == "CALL" else "put"
        prem = float(e.get("entry_premium") or 0)
        strike, expiry = c.get("strike"), c.get("expiry")
        if strike and expiry:
            exp = date.fromisoformat(str(expiry)[:10])

            def rp(spot, days, strike=float(strike), exp=exp, kind=kind, qty=qty, sym=e["ticker"]):
                yrs = max((exp - date.today()).days - days, 0) / 365
                return bs_price(spot, strike, yrs, vol_of(sym), kind, 0.065) * qty
        else:                                   # logged by hand without the contract: a 0.5-delta approximation
            def rp(spot, days, prem=prem, qty=qty, kind=kind, base={}):
                s0 = base.setdefault("s0", spot)
                return max(prem + (0.5 if kind == "call" else -0.5) * (spot - s0), 0.0) * qty
        out.append(Position("My trades", f"{e.get('contract') or e['ticker']} ({kind})", e["ticker"], "option", rp,
                            e.get("stop"), extra={"id": e.get("id"), "approx": not (strike and expiry)}))
    return out


def from_holdings(rows: list[dict], fx: float) -> list[Position]:
    out = []
    for h in rows:
        qty = float(h.get("qty") or 0)
        if qty <= 0 or not h.get("symbol"):
            continue
        mult = 1.0 if h["symbol"].upper().endswith((".NS", ".BO")) else fx

        def rp(spot, days, qty=qty, mult=mult):
            return spot * qty * mult
        out.append(Position("Holdings", h.get("name") or h["symbol"].replace(".NS", ""), h["symbol"], "holding", rp,
                            h.get("stop"), has_stop_concept=False, extra={"id": h.get("id")}))
    return out


# ----------------------------------------------------------------------------- analysis
def analyze(positions: list[Position], hist: dict[str, pd.Series], bench: pd.Series, capital: float,
            sectors: dict[str, str]) -> dict:
    if not positions:
        return {"positions": [], "capital": capital, "empty": True}
    bench_r = bench.pct_change()
    rows, pnl_paths = [], []
    for p in positions:
        s = hist.get(p.symbol)
        if s is None or s.dropna().empty:
            rows.append({"source": p.source, "label": p.label, "symbol": p.symbol, "kind": p.kind, "error": "no price history"})
            continue
        s = s.dropna()
        spot = float(s.iloc[-1])
        v0 = p.reprice(spot, 0)
        bump = spot * 0.001
        delta_inr = (p.reprice(spot + bump, 0) - p.reprice(spot - bump, 0)) / (2 * bump) * spot   # ₹ per 100% move
        r = s.pct_change()
        joined = pd.concat([r, bench_r], axis=1).dropna().iloc[-250:]
        beta = float(joined.iloc[:, 0].cov(joined.iloc[:, 1]) / joined.iloc[:, 1].var()) if len(joined) > 60 else 1.0
        sh = lambda pct: p.reprice(spot * (1 + beta * pct), 0) - v0
        at_stop = None
        if p.stop:
            at_stop = min(p.reprice(float(p.stop), 0) - v0, 0.0)
        hist_r = r.dropna().iloc[-500:]
        path = pd.Series([p.reprice(spot * (1 + x), 1) - v0 for x in hist_r.to_numpy()], index=hist_r.index)
        pnl_paths.append(path.rename(p.label))
        sector = ("Crypto" if p.kind == "crypto" else "Commodities" if p.kind == "commodity"
                  else "Index funds / ETFs" if is_etf(p.symbol) else sectors.get(p.symbol, "Other"))
        rows.append({"source": p.source, "label": p.label, "symbol": p.symbol, "kind": p.kind, "sector": sector,
                     "spot": spot, "value": v0, "exposure": delta_inr, "beta": beta,
                     "beta_exposure": delta_inr * beta, "loss_at_stop": at_stop,
                     "missing_stop": p.has_stop_concept and not p.stop, "down5": sh(-0.05), "up5": sh(0.05),
                     "daily_vol": float(r.dropna().iloc[-60:].std()), **p.extra})
    ok = [x for x in rows if "error" not in x]
    gross = sum(abs(x["exposure"]) for x in ok)
    net = sum(x["exposure"] for x in ok)
    bexp = sum(x["beta_exposure"] for x in ok)
    stops = sum(x["loss_at_stop"] or 0 for x in ok)
    pnl = pd.concat(pnl_paths, axis=1).dropna() if pnl_paths else pd.DataFrame()
    total = pnl.sum(axis=1) if not pnl.empty else pd.Series(dtype=float)
    var = es = None
    if len(total) >= 100:
        q = float(np.percentile(total, 5))
        var, es = -q, -float(total[total <= q].mean())
    sec = {}
    for x in ok:
        sec[x["sector"]] = sec.get(x["sector"], 0.0) + abs(x["exposure"])
    sectors_out = sorted(({"sector": k, "exposure": v, "share": v / gross if gross else 0} for k, v in sec.items()),
                         key=lambda d: -d["exposure"])
    # correlation of the biggest positions' daily moves
    big = sorted(ok, key=lambda x: -abs(x["exposure"]))[:8]
    syms = list(dict.fromkeys(x["symbol"] for x in big))
    corr, avg_corr = None, None
    if len(syms) >= 2:
        rets = pd.concat({s_: hist[s_].pct_change() for s_ in syms}, axis=1).dropna().iloc[-250:]
        if len(rets) > 60:
            cm = rets.corr()
            corr = {"symbols": [s_.replace(".NS", "") for s_ in syms], "matrix": cm.round(2).values.tolist()}
            iu = np.triu_indices(len(syms), 1)
            avg_corr = float(cm.values[iu].mean())
    warn = []
    if capital and -stops > LIMITS["risk_at_stops"] * capital:
        warn.append(f"If every stop is hit you lose {-stops / capital:.1%} of your capital; keep it under {LIMITS['risk_at_stops']:.0%}.")
    for x in ok:
        if capital and abs(x["exposure"]) > LIMITS["single"] * capital and not is_etf(x["symbol"]):
            warn.append(f"{x['label']} is {abs(x['exposure']) / capital:.0%} of your capital in one position.")
    for sx in sectors_out:                    # an index fund is already spread over many companies
        if sx["sector"] != "Index funds / ETFs" and gross and sx["share"] > LIMITS["sector"] and len(ok) > 1:
            warn.append(f"{sx['share']:.0%} of your exposure is in {sx['sector']}.")
    if avg_corr is not None and avg_corr > LIMITS["correlation"]:
        warn.append(f"Your biggest positions move together (average correlation {avg_corr:.2f}): they will tend to win and lose at the same time.")
    if capital and bexp > LIMITS["beta_exposure"] * capital:
        warn.append(f"You effectively own {bexp / capital:.1f}x your capital of the market: a 10% Nifty fall costs about {0.1 * bexp / capital:.0%}.")
    missing = [x["label"] for x in ok if x["missing_stop"]]
    if missing:
        warn.append("No stop-loss on: " + ", ".join(missing[:6]) + (" ..." if len(missing) > 6 else "") + ".")
    return {"positions": rows, "capital": capital, "gross": gross, "net": net, "beta_exposure": bexp,
            "risk_at_stops": stops, "down5": sum(x["down5"] for x in ok), "up5": sum(x["up5"] for x in ok),
            "var95": var, "es95": es, "days": int(len(total)), "sectors": sectors_out, "correlation": corr,
            "avg_correlation": avg_corr, "warnings": warn, "limits": LIMITS, "empty": False}
