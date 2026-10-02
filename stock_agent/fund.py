"""Model fund: a rules-based equity portfolio built the way systematic fund houses build factor funds.

Every rebalance date (month or quarter end):
  1. Universe: Nifty 200 stocks with a year of history and at least about ₹2 crore traded a day.
  2. Scores, from prices up to that date only:
       momentum      return from 12 months ago to 1 month ago, divided by 1-year volatility
                     (the "risk-adjusted 12-1 momentum" that NSE's momentum indices use)
       low volatility  minus the 1-year volatility of daily returns
     Each is turned into a z-score across the universe; score = momentum z + 0.5 x low-volatility z.
  3. Hold the top 25, equal weight, at most 5 from one industry.
These definitions are textbook and fixed in advance; nothing is tuned on the backtest.

Backtest: daily total-return prices (dividends included), positions held between rebalance dates,
0.25% cost on every rupee traded (brokerage, STT, spread and impact). Two benchmarks: the Nifty 50 ETF
(NIFTYBEES), and an equal-weight basket of the whole universe. The second one matters: today's index
members are, by definition, the companies that survived and grew, so any portfolio drawn from them looks
good in hindsight (survivorship bias). The factor strategy is only adding value if it beats that basket.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import DEFAULT_CACHE, _cache_path, read_cached, refresh_many
from .screener import load_universe
from .universes import get_universe, industries

BENCHMARK = "NIFTYBEES.NS"
COST = 0.0025
STCG, LTCG = 0.20, 0.125      # Indian listed-equity capital-gains tax (held under / over 12 months)


@dataclass
class FundRules:
    universe: str = "nifty200"
    size: int = 25
    per_industry: int = 5
    rebalance: str = "M"            # "M" monthly or "Q" quarterly
    lowvol_weight: float = 0.5
    min_turnover: float = 2e7


def scores(closes: pd.DataFrame, values: pd.DataFrame, date: pd.Timestamp, rules: FundRules) -> pd.DataFrame:
    """Factor scores for every eligible stock on `date`, using data up to and including that day only."""
    hist = closes.loc[:date]
    if len(hist) < 260:
        return pd.DataFrame()
    window = hist.iloc[-253:]
    ok = window.notna().sum() >= 240
    liquid = values.loc[:date].iloc[-60:].median() >= rules.min_turnover
    eligible = ok & liquid.reindex(ok.index).fillna(False)
    w = window.loc[:, eligible[eligible].index]
    if w.shape[1] < rules.size:
        return pd.DataFrame()
    rets = w.pct_change().iloc[1:]
    vol = rets.std() * math.sqrt(252)
    mom = w.iloc[-22] / w.iloc[0] - 1                     # 12 months ago -> 1 month ago
    ram = mom / vol
    z = lambda x: (x - x.mean()) / x.std(ddof=0)
    out = pd.DataFrame({"momentum_12_1": mom, "volatility": vol, "risk_adj_momentum": ram})
    out["score"] = z(ram) + rules.lowvol_weight * z(-vol)
    return out.dropna().sort_values("score", ascending=False)


def select(sc: pd.DataFrame, sectors: dict[str, str], rules: FundRules) -> list[str]:
    picks, count = [], {}
    for t in sc.index:
        ind = sectors.get(t, "Other")
        if count.get(ind, 0) >= rules.per_industry:
            continue
        picks.append(t)
        count[ind] = count.get(ind, 0) + 1
        if len(picks) == rules.size:
            break
    return picks


def _rebalance_dates(idx: pd.DatetimeIndex, freq: str) -> list[pd.Timestamp]:
    s = pd.Series(idx, index=idx)
    key = idx.to_period("Q" if freq == "Q" else "M")
    return list(s.groupby(key).max())


def backtest(closes: pd.DataFrame, values: pd.DataFrame, bench: pd.Series, sectors: dict[str, str],
             rules: FundRules, progress=None) -> dict:
    daily = closes.pct_change()
    dates = [d for d in _rebalance_dates(closes.index, rules.rebalance) if len(closes.loc[:d]) >= 260]
    nav, ew_nav, bench_nav, turnover, holdings_log = [1.0], [1.0], [1.0], [], []
    nav_dates = [dates[0]]
    prev = {}
    # after-tax account for a person doing this themselves: positions with cost and buy date
    tax_nav = [1.0]
    lots: dict[str, list[float]] = {}          # ticker -> [value, cost, buy_date_ordinal]
    loss_cf = 0.0
    tax_paid = 0.0
    for k, (d0, d1) in enumerate(zip(dates[:-1], dates[1:])):
        if progress and k % 6 == 0:
            progress(f"Backtesting {d0:%b %Y}", 0.3 + 0.6 * k / len(dates))
        sc = scores(closes, values, d0, rules)
        if sc.empty:
            continue
        picks = select(sc, sectors, rules)
        target = {t: 1 / len(picks) for t in picks}
        traded = sum(abs(target.get(t, 0) - prev.get(t, 0)) for t in set(target) | set(prev))
        turnover.append(traded / 2)
        period = daily.loc[(daily.index > d0) & (daily.index <= d1)]
        # buy-and-hold inside the period: weights drift with prices
        grow = (1 + period[picks].fillna(0)).prod()
        r = float((grow * pd.Series(target)).sum() - 1) - COST * traded
        elig = sc.index
        ew = float((1 + period[elig].fillna(0)).prod().mean() - 1)
        b = float((1 + bench.loc[(bench.index > d0) & (bench.index <= d1)].pct_change().fillna(0)).prod() - 1) \
            if False else float(bench.loc[:d1].iloc[-1] / bench.loc[:d0].iloc[-1] - 1)
        # ---- taxed account: trade to target at d0, pay tax on net realised gains, hold to d1
        acct = sum(v[0] for v in lots.values()) if lots else tax_nav[-1]
        gains_st = gains_lt = 0.0
        for t in list(lots):
            want = target.get(t, 0) * acct
            val, cost, bought = lots[t]
            if val > want + 1e-12:                      # selling part or all
                frac = (val - want) / val
                g = (val - cost) * frac
                if (d0.toordinal() - bought) < 365:
                    gains_st += g
                else:
                    gains_lt += g
                lots[t] = [val - (val - want), cost * (1 - frac), bought]
        net = gains_st + gains_lt + loss_cf
        tax = 0.0
        if net > 0:
            st_share = max(gains_st, 0) / max(gains_st + max(gains_lt, 0), 1e-12)
            tax = net * (st_share * STCG + (1 - st_share) * LTCG)
            loss_cf = 0.0
        else:
            loss_cf = net
        tax_paid += tax
        acct -= tax + COST * traded * acct
        for t, w_ in target.items():                     # buy up to target with what is left
            want = w_ * acct
            val, cost, bought = lots.get(t, [0.0, 0.0, d0.toordinal()])
            if want > val:
                lots[t] = [want, cost + (want - val), bought if val > 0 else d0.toordinal()]
            else:
                lots[t] = [want, cost * (want / val) if val else 0.0, bought]
        lots = {t: v for t, v in lots.items() if v[0] > 1e-12}
        for t in lots:
            lots[t][0] *= float(grow.get(t, 1.0))
        tax_nav.append(sum(v[0] for v in lots.values()))

        nav.append(nav[-1] * (1 + r))
        ew_nav.append(ew_nav[-1] * (1 + ew))
        bench_nav.append(bench_nav[-1] * (1 + b))
        nav_dates.append(d1)
        drift = grow * pd.Series(target)
        prev = (drift / drift.sum()).to_dict()
        holdings_log.append({"date": str(d0.date()), "picks": picks})
    curve = pd.DataFrame({"fund": nav, "equal_weight": ew_nav, "nifty": bench_nav}, index=pd.DatetimeIndex(nav_dates))
    per_year = 12 if rules.rebalance == "M" else 4
    years = (curve.index[-1] - curve.index[0]).days / 365.25
    # selling everything at the end, after tax, against an index fund taxed once at the end
    unreal = sum(v[0] - v[1] for v in lots.values())
    end_tax = max(unreal + loss_cf, 0) * LTCG
    fund_after = tax_nav[-1] - end_tax
    nifty_after = 1 + (bench_nav[-1] - 1) * (1 - LTCG)
    after_tax = {"fund_growth_of_1": fund_after, "fund_cagr": fund_after ** (1 / years) - 1,
                 "nifty_growth_of_1": nifty_after, "nifty_cagr": nifty_after ** (1 / years) - 1,
                 "tax_paid_along_the_way": tax_paid}
    curve["fund_after_tax"] = tax_nav
    return {"curve": curve, "metrics": {c: _metrics(curve[c], per_year) for c in ("fund", "equal_weight", "nifty")},
            "after_tax": after_tax,
            "turnover_per_year": float(np.mean(turnover) * per_year) if turnover else 0.0,
            "beat_nifty_12m": _beat(curve["fund"], curve["nifty"], per_year),
            "beat_equal_12m": _beat(curve["fund"], curve["equal_weight"], per_year),
            "yearly": _yearly(curve), "log": holdings_log}


def _metrics(navs: pd.Series, per_year: int) -> dict:
    years = (navs.index[-1] - navs.index[0]).days / 365.25
    r = navs.pct_change().dropna()
    dd = (navs / navs.cummax() - 1).min()
    cagr = navs.iloc[-1] ** (1 / years) - 1 if years > 0 else 0.0
    vol = r.std() * math.sqrt(per_year)
    return {"cagr": float(cagr), "vol": float(vol), "max_drawdown": float(dd), "years": float(years),
            "growth_of_1": float(navs.iloc[-1]), "return_per_risk": float(cagr / vol) if vol else None}


def _beat(a: pd.Series, b: pd.Series, per_year: int) -> float | None:
    ra, rb = a.pct_change(per_year).dropna(), b.pct_change(per_year).dropna()
    return float((ra > rb).mean()) if len(ra) else None


def _yearly(curve: pd.DataFrame) -> list[dict]:
    y = curve.groupby(curve.index.year).last()
    first = curve.iloc[0]
    prev = pd.concat([first.to_frame().T, y.iloc[:-1]]).set_axis(y.index)
    out = (y / prev - 1)
    counts = curve.groupby(curve.index.year).size()
    keep = [i for i in out.index if not (i == curve.index[0].year and counts[i] < 2)]  # a stub first year says nothing
    return [{"year": int(i), "partial": bool(i in (curve.index[0].year, curve.index[-1].year)),
             **{c: float(out.loc[i, c]) for c in out}} for i in keep]


def run(rules: FundRules = FundRules(), capital: float = 200_000, offline: bool = False, cache_dir=DEFAULT_CACHE,
        progress=None) -> dict:
    progress = progress or (lambda m, f: None)
    tickers = get_universe(rules.universe, offline)
    prices = load_universe(tickers, "10y", "1d", offline, cache_dir, log=lambda *a: None,
                           progress=lambda m, f: progress(m, f * 0.4))
    if not offline:
        refresh_many([BENCHMARK], "10y", "1d", cache_dir)
    bench = read_cached(_cache_path(cache_dir, BENCHMARK, "10y", "1d"))["Close"]
    closes = pd.DataFrame({t: df["Close"] for t, df in prices.items()}).sort_index()
    values = pd.DataFrame({t: df["Close"] * df["Volume"] for t, df in prices.items()}).reindex(closes.index)
    closes = closes.loc[closes.index >= bench.index[0]]
    values = values.loc[closes.index]
    sectors = industries(offline)
    bt = backtest(closes, values, bench, sectors, rules, progress)

    # today's portfolio
    today = closes.index[-1]
    sc = scores(closes, values, today, rules)
    picks = select(sc, sectors, rules)
    last_rebal = bt["log"][-1]["picks"] if bt["log"] else []
    # fit the portfolio to the capital: stocks whose single share costs more than an equal slot are left out,
    # and the money is shared equally among the rest
    price_of = {t: float(closes[t].dropna().iloc[-1]) for t in picks}
    buyable = list(picks)
    while buyable and any(price_of[t] > capital / len(buyable) for t in buyable):
        worst = max(buyable, key=lambda t: price_of[t])
        buyable.remove(worst)
    too_dear = [t for t in picks if t not in buyable]
    per_stock = capital / max(len(buyable), 1)
    holdings = []
    for t in picks:
        px = float(closes[t].dropna().iloc[-1])
        shares = int(per_stock // px) if t in buyable else 0
        holdings.append({"ticker": t, "industry": sectors.get(t, "Other"), "price": px, "shares": shares,
                         "amount": shares * px, "momentum_12_1": float(sc.loc[t, "momentum_12_1"]),
                         "volatility": float(sc.loc[t, "volatility"]), "score": float(sc.loc[t, "score"]),
                         "new": t not in last_rebal})
    progress("Done", 1.0)
    fit = None
    if too_dear:
        fit = (f"With this capital {len(too_dear)} of {len(picks)} stocks cost more than an equal share of the money "
               f"({', '.join(t.replace('.NS', '') for t in too_dear)}), so the money is spread over the other "
               f"{len(buyable)}. Add capital to hold all {len(picks)}, or use a factor index fund, which holds every stock "
               f"in the right weight.")
    return {"rules": rules.__dict__, "as_of": str(today.date()), "capital": capital, "holdings": holdings, "fit_note": fit,
            "invested": sum(h["amount"] for h in holdings), "dropped": [t for t in last_rebal if t not in picks],
            "universe_size": int(len(sc)), **bt}
