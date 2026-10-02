"""Long-term goal planner: how much to invest, in what mix, and how likely the goal is.

Asset classes and their history:
  Equity  Nifty 50 ETF (NIFTYBEES.NS)   ten years of monthly returns
  Gold    Gold ETF (GOLDBEES.NS)        ten years of monthly returns
  Debt    a short-duration debt fund / FD style return, assumed 7% a year with 2% volatility
          (there is no clean free price history for Indian debt funds)

Expected returns are the historical ones, but equity is capped at 12% a year and gold at 9%, because one
strong decade is not a promise. Monthly returns are simulated 5,000 times with the assets' own volatility
and correlation, the portfolio is rebalanced to the target mix every month, and the monthly investment
grows by the step-up each year. The goal is in today's money and grows with inflation.

This is a planning calculator, not personal financial advice.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .data import DEFAULT_CACHE, _cache_path, read_cached, refresh_many
from .sizing import money

PROXIES = {"equity": "NIFTYBEES.NS", "gold": "GOLDBEES.NS"}
CAPS = {"equity": 0.12, "gold": 0.09}
DEBT = (0.07, 0.02)
PROFILES = {"conservative": 0.35, "balanced": 0.60, "aggressive": 0.80}
INSTRUMENTS = {
    "equity": "Nifty 50 index fund or ETF (low cost, broad market)",
    "debt": "short-duration debt fund, PPF or fixed deposits",
    "gold": "gold ETF or sovereign gold bonds",
}


@dataclass
class Goal:
    name: str = "My goal"
    target_today: float = 2_000_000      # in today's rupees
    years: float = 10
    current: float = 0                   # already saved towards it
    monthly: float = 10_000              # monthly investment (SIP)
    step_up: float = 0.05                # SIP grows this much every year
    inflation: float = 0.06
    profile: str = "balanced"


def allocation(years: float, profile: str) -> dict[str, float]:
    """Target mix: more equity for long horizons and higher risk appetite, almost none for short goals."""
    eq = PROFILES.get(profile, 0.60)
    if years < 3:
        eq = min(eq, 0.15)
    elif years < 5:
        eq = min(eq, 0.40)
    elif years >= 12:
        eq = min(eq + 0.10, 0.85)
    gold = 0.10 if years >= 3 else 0.05
    return {"equity": round(eq, 2), "gold": gold, "debt": round(1 - eq - gold, 2)}


def market_stats(offline: bool = False, cache_dir=DEFAULT_CACHE) -> dict:
    """Annual expected return, volatility and the equity-gold correlation from ten years of monthly data."""
    if not offline:
        refresh_many(list(PROXIES.values()), "10y", "1d", cache_dir)
    monthly = {}
    for k, sym in PROXIES.items():
        path = _cache_path(cache_dir, sym, "10y", "1d")
        df = read_cached(path)
        monthly[k] = df["Close"].resample("ME").last().pct_change().dropna()
    m = pd.DataFrame(monthly).dropna()
    out = {}
    for k in PROXIES:
        years = len(m) / 12
        cagr = float((1 + m[k]).prod() ** (1 / years) - 1)
        out[k] = {"hist_cagr": cagr, "expected": min(cagr, CAPS[k]), "vol": float(m[k].std() * math.sqrt(12)),
                  "proxy": PROXIES[k], "years": round(years, 1)}
    out["debt"] = {"hist_cagr": None, "expected": DEBT[0], "vol": DEBT[1], "proxy": "assumed", "years": None}
    out["corr_equity_gold"] = float(m["equity"].corr(m["gold"]))
    return out


def _simulate(goal: Goal, mix: dict, stats: dict, monthly_sip: float, paths: int = 5000, seed: int = 7) -> np.ndarray:
    """Corpus after each year for every path: shape (paths, years + 1)."""
    months = int(round(goal.years * 12))
    keys = ["equity", "gold", "debt"]
    mu = np.array([stats[k]["expected"] for k in keys])
    vol = np.array([stats[k]["vol"] for k in keys])
    corr = np.eye(3)
    corr[0, 1] = corr[1, 0] = stats["corr_equity_gold"]
    cov_m = np.outer(vol, vol) * corr / 12
    mu_m = np.log1p(mu) / 12 - np.diag(cov_m) / 2          # lognormal monthly drift giving the annual mean
    rng = np.random.default_rng(seed)
    z = rng.multivariate_normal(mu_m, cov_m, size=(paths, months))
    w = np.array([mix[k] for k in keys])
    port = (np.expm1(z) * w).sum(axis=2)                   # monthly rebalanced portfolio return
    value = np.full(paths, float(goal.current))
    yearly = [value.copy()]
    for t in range(months):
        sip = monthly_sip * (1 + goal.step_up) ** (t // 12)
        value = (value + sip) * (1 + port[:, t])
        if (t + 1) % 12 == 0 or t == months - 1:
            yearly.append(value.copy())
    return np.array(yearly).T


def plan(goal: Goal, offline: bool = False, cache_dir=DEFAULT_CACHE, stats: dict | None = None) -> dict:
    stats = stats or market_stats(offline, cache_dir)
    mix = allocation(goal.years, goal.profile)
    target = goal.target_today * (1 + goal.inflation) ** goal.years
    sims = _simulate(goal, mix, stats, goal.monthly)
    final = sims[:, -1]
    prob = float((final >= target).mean())

    def prob_for(sip):
        return float((_simulate(goal, mix, stats, sip, paths=2000)[:, -1] >= target).mean())

    lo, hi = 0.0, max(goal.monthly, 1000.0)
    while prob_for(hi) < 0.75 and hi < 1e8:
        hi *= 2
    for _ in range(22):                                     # binary search: SIP with a 75% chance
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if prob_for(mid) < 0.75 else (lo, mid)
    needed = math.ceil(hi / 100) * 100
    exp_return = sum(mix[k] * stats[k]["expected"] for k in mix)
    invested = goal.current + sum(goal.monthly * 12 * (1 + goal.step_up) ** y for y in range(int(math.ceil(goal.years))))
    bands = [{"year": y, "p10": float(np.percentile(sims[:, y], 10)), "p50": float(np.percentile(sims[:, y], 50)),
              "p90": float(np.percentile(sims[:, y], 90))} for y in range(sims.shape[1])]
    return {
        "goal": asdict(goal), "target_future": target, "mix": mix, "expected_return": exp_return,
        "probability": prob, "median": float(np.median(final)), "p10": float(np.percentile(final, 10)),
        "p90": float(np.percentile(final, 90)), "needed_monthly_75": needed, "invested": invested,
        "bands": bands, "stats": stats,
        "instruments": {k: INSTRUMENTS[k] for k in mix},
        "monthly_split": {k: round(goal.monthly * v) for k, v in mix.items()},
    }


def render_text(p: dict) -> str:
    g = p["goal"]
    lines = [f"{g['name']}: {money(g['target_today'], '₹')} in today's money in {g['years']:g} years "
             f"= {money(p['target_future'], '₹')} at {g['inflation']:.0%} inflation",
             f"Mix ({g['profile']}): " + ", ".join(f"{k} {v:.0%}" for k, v in p["mix"].items()) +
             f" · expected {p['expected_return']:.1%} a year",
             f"With {money(g['monthly'], '₹')}/month (+{g['step_up']:.0%} a year) and {money(g['current'], '₹')} now: "
             f"{p['probability']:.0%} chance of reaching it; median {money(p['median'], '₹')} "
             f"(bad case {money(p['p10'], '₹')}, good case {money(p['p90'], '₹')})",
             f"For a 75% chance invest about {money(p['needed_monthly_75'], '₹')} a month.",
             "Monthly split: " + ", ".join(f"{k} {money(v, '₹')}" for k, v in p["monthly_split"].items()),
             "Where: " + "; ".join(f"{k}: {v}" for k, v in p["instruments"].items()),
             "Planning estimate from past returns, not a guarantee or personal advice."]
    return "\n".join(lines)
