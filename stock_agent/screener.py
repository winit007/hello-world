"""Screen a universe of stocks and pick the N best setups right now.

For every stock we look for candlestick patterns that completed in the last few bars and ask:
how often did this exact pattern work on this stock, over the chosen holding period?

Small samples lie, so each stock's own win rate is shrunk toward the same pattern's win rate across
the whole universe (a Bayesian average with `prior_strength` pseudo-signals):

    success = (wins_on_stock + k * pooled_rate) / (signals_on_stock + k)

A stock with 200 past signals keeps essentially its own rate; one with 8 signals mostly inherits the
universe's. News sentiment then tilts the score: headlines agreeing with the trade add up to
`news_weight`, disagreeing headlines subtract it, and strongly contradicting news vetoes the pick.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .backtest import evaluate_rules, pool_rules
from .tradetest import allowed_mask, load_strategy, pooled_stats, trade_outcomes, trade_stats
from .data import DEFAULT_CACHE, load_prices
from .news import load_news, summarize
from .patterns import BULLISH, PATTERN_BY_NAME, detect_all


@dataclass
class Pick:
    ticker: str
    pattern: str
    direction: str
    signal_date: str
    last_close: float
    horizon: int
    n: int                    # signals of this pattern on this stock
    raw_win_rate: float       # this stock only
    pooled_win_rate: float    # whole universe
    success: float            # shrunk estimate
    baseline: float
    avg_return: float
    news_mean: float = 0.0
    news_count: int = 0
    news_aligned: float = 0.0     # -1..1, positive = headlines agree with the trade
    score: float = 0.0
    trend: str = ""
    recent_n: int = 0             # signals in the most recent `recent_bars`
    recent_win_rate: float = float("nan")
    plan: object = None           # sizing.TradePlan once sized
    notes: list[str] = field(default_factory=list)

    @property
    def side(self) -> str:
        return "CALL" if self.direction == BULLISH else "PUT"

    @property
    def edge(self) -> float:
        return self.success - self.baseline


def _trend(close: pd.Series) -> str:
    s20, s50, last = close.rolling(20).mean().iloc[-1], close.rolling(50).mean().iloc[-1], close.iloc[-1]
    return "up" if last > s20 > s50 else "down" if last < s20 < s50 else "flat"


def load_universe(tickers, period, interval, offline, cache_dir, log=print, progress=None) -> dict[str, pd.DataFrame]:
    done = [0]

    def one(t):
        try:
            df = load_prices(t, period, interval, offline, cache_dir)
            out = t, df if len(df) >= 250 else None
        except Exception as exc:
            log(f"[screen] skip {t}: {exc}")
            out = t, None
        done[0] += 1
        if progress:
            progress(f"Loading prices {done[0]}/{len(tickers)}", 0.6 * done[0] / len(tickers))
        return out

    with ThreadPoolExecutor(max_workers=8) as ex:
        return {t: df for t, df in ex.map(one, tickers) if df is not None}


def screen(
    tickers: list[str],
    period: str = "10y",
    interval: str = "1d",
    horizon: int = 5,
    lookback: int = 3,
    top: int = 5,
    prior_strength: int = 20,
    min_edge: float = 0.02,
    news_weight: float = 0.10,
    offline: bool = False,
    cache_dir=None,
    use_context: bool = True,
    recent_bars: int = 750,
    log=print,
    size: bool = True,
    capital: float | None = None,
    risk_pct: float = 0.02,
    affordable_only: bool = False,
    progress=None,
    prices: dict | None = None,
    use_chain: bool = True,
    outcomes: dict | None = None,
) -> tuple[list[Pick], list[Pick], dict]:
    """Return (top picks, every candidate ranked, stats).

    With `affordable_only`, picks are taken in rank order but only those where at least one lot fits
    the risk budget, so the list is always tradeable (possibly with weaker setups).
    """
    progress = progress or (lambda msg, frac: None)
    cache_dir = cache_dir or DEFAULT_CACHE
    kw = {"cache_dir": cache_dir}
    if prices is None:  # callers replaying past days pass prices already cut off at that day
        prices = load_universe(tickers, period, interval, offline, cache_dir, log, progress)
    progress("Backtesting candlestick rules", 0.65)
    log(f"[screen] {len(prices)}/{len(tickers)} stocks loaded; backtesting {horizon}-bar rules ...")

    # every past signal replayed as the actual trade (see tradetest.py); computed once per stock
    if outcomes is None:
        outcomes = {t: trade_outcomes(df, t, horizon, use_context) for t, df in prices.items()}
    stats = {t: trade_stats(outcomes[t], as_of=df.index[-1]) for t, df in prices.items() if t in outcomes}
    pooled = pooled_stats(stats)

    # ---- candidates: best active setup per stock
    candidates: list[Pick] = []
    for t, df in prices.items():
        if t not in stats:
            continue
        st = stats[t].set_index("pattern")
        signals = detect_all(df, use_context=use_context)
        strat = load_strategy()
        allow_bull, allow_bear = allowed_mask(df, True, strat, None), allowed_mask(df, False, strat, None)
        for name in signals.columns:  # a setup against the strategy's trend filter is not a trade
            ok = allow_bull if PATTERN_BY_NAME[name].direction == BULLISH else allow_bear
            signals[name] = signals[name].to_numpy() & ok
        recent = signals.tail(lookback)
        active = {}
        for date, row in recent.iterrows():           # newest occurrence of each pattern wins
            for name in row.index[row.to_numpy()]:
                active[name] = date
        best = None
        for name, date in active.items():
            if name not in pooled.index or pooled.loc[name, "n"] < 30:
                continue
            direction = PATTERN_BY_NAME[name].direction
            p_rate, p_ret = float(pooled.loc[name, "win_rate"]), float(pooled.loc[name, "avg_ret"])
            has = name in st.index
            n = int(st.loc[name, "n"]) if has else 0
            wins = int(st.loc[name, "wins"]) if has else 0
            sum_ret = float(st.loc[name, "sum_ret"]) if has else 0.0
            base_row = pooled.loc["_baseline_" + direction] if "_baseline_" + direction in pooled.index else None
            baseline = (float(st.loc[name, "baseline"]) if has and not pd.isna(st.loc[name, "baseline"])
                        else float(base_row["win_rate"]) if base_row is not None else 0.5)
            pick = Pick(
                ticker=t, pattern=name, direction=direction, signal_date=str(date.date()),
                last_close=float(df["Close"].iloc[-1]), horizon=horizon, n=n,
                raw_win_rate=(wins / n) if n else float("nan"), pooled_win_rate=p_rate,
                success=(wins + prior_strength * p_rate) / (n + prior_strength), baseline=baseline,
                avg_return=(sum_ret + prior_strength * p_ret) / (n + prior_strength),
                trend=_trend(df["Close"]),
            )
            if pick.edge < min_edge or pick.avg_return <= 0:
                continue
            if best is None or pick.success > best.success:
                if best is not None and best.direction != pick.direction:
                    pick.notes.append(f"conflicting {best.pattern} ({best.direction}) also active")
                best = pick
            elif best.direction != pick.direction:
                best.notes.append(f"conflicting {name} ({pick.direction}) also active")
        if best and any(n.startswith("conflicting") for n in best.notes):
            log(f"[screen] {t}: skipped, bullish and bearish setups with an edge are both active")
            best = None
        if best:
            # does the setup still work as a trade in the most recent years?
            since = df.index[-1] - pd.Timedelta(days=int(recent_bars * 365 / 250))
            rs = trade_stats(outcomes[t], as_of=df.index[-1], since=since).set_index("pattern")
            if best.pattern in rs.index:
                best.recent_n = int(rs.loc[best.pattern, "n"])
                best.recent_win_rate = float(rs.loc[best.pattern, "win_rate"])
                if best.recent_n >= 5 and best.recent_win_rate < best.baseline:
                    best.notes.append(f"edge faded: {best.recent_win_rate:.0%} in the last {recent_bars // 250}y")
            candidates.append(best)
    log(f"[screen] {len(candidates)} stocks have an active setup with a historical edge; reading news ...")
    progress(f"Reading news for {len(candidates)} stocks with a setup", 0.75)

    # ---- news for candidates only
    def news(p: Pick):
        if not news_weight:
            return p, {"count": 0, "mean": 0.0}
        try:
            s = summarize(load_news(p.ticker, None, offline, **kw))
        except Exception as exc:
            log(f"[screen] news failed for {p.ticker}: {exc}")
            s = {"count": 0, "mean": 0.0}
        return p, s

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(news, candidates))

    ranked = []
    for p, s in results:
        p.news_mean, p.news_count = float(s["mean"]), int(s["count"])
        sign = 1 if p.direction == BULLISH else -1
        p.news_aligned = float(np.clip(sign * p.news_mean * 4, -1, 1)) if p.news_count else 0.0
        p.score = p.success + news_weight * p.news_aligned
        if p.news_aligned <= -0.6:
            p.notes.append("news strongly contradicts the setup; excluded")
            continue
        if PATTERN_BY_NAME[p.pattern].context is None and (
            (p.direction == BULLISH and p.trend == "down") or (p.direction != BULLISH and p.trend == "up")
        ):
            p.notes.append(f"continuation pattern against the {p.trend}trend")
        if p.news_aligned <= -0.2:
            p.notes.append("news leans against the trade")
        ranked.append(p)
    ranked.sort(key=lambda p: p.score, reverse=True)
    if size:
        from . import options, sizing

        progress("Sizing orders", 0.9)
        chosen = []
        for p in ranked:
            if len(chosen) >= top:
                break
            try:
                chain = options.load_chain(p.ticker, offline, cache_dir) if use_chain else None
                p.plan = sizing.plan_trade(p.ticker, prices[p.ticker], p.direction, horizon, p.pattern,
                                           p.signal_date, chain, capital, risk_pct, offline=offline, cache_dir=cache_dir)
            except Exception as exc:
                log(f"[screen] sizing failed for {p.ticker}: {exc}")
            if not affordable_only or (p.plan is not None and p.plan.lots > 0):
                chosen.append(p)
        if affordable_only:
            ranked = chosen + [p for p in ranked if p not in chosen]
            picks_override = chosen
        else:
            picks_override = None
    else:
        picks_override = None
    as_of = max((df.index[-1] for df in prices.values()), default=None)
    stats = {"universe": len(tickers), "loaded": len(prices), "candidates": len(candidates), "eligible": len(ranked),
             "as_of": str(as_of.date()) if as_of is not None else ""}
    progress("Done", 1.0)
    return (picks_override if picks_override is not None else ranked[:top]), ranked, stats


def render_markdown(picks: list[Pick], ranked: list[Pick], stats: dict, horizon: int, top: int) -> str:
    as_of = stats.get("as_of") or max((p.signal_date for p in ranked), default="")
    md = [f"# Top {top} setups", "",
          f"*Market data to {as_of} · {stats['loaded']} stocks scanned · {stats['candidates']} with an active "
          f"setup that has a historical edge · holding period {horizon} trading days*", ""]
    if validation_line():
        md += [f"> {validation_line()}", ""]
    if not picks:
        md.append("_No stock has an active candlestick setup with a historical edge right now. That is a valid "
                  "answer: sit out, or widen `--lookback`._")
        return "\n".join(md)
    if len(picks) < top:
        md += [f"Only {len(picks)} stocks qualify today; the rest have no setup with an edge.", ""]
    md += ["| # | Stock | Trade | Setup | Signal | Success rate | Baseline | Stock history | Last 3y | Universe | Avg move | News | Score |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, p in enumerate(picks, 1):
        own = f"{p.raw_win_rate:.0%} of {p.n}" if p.n else "none"
        news = f"{p.news_mean:+.2f} ({p.news_count})" if p.news_count else "n/a"
        recent = f"{p.recent_win_rate:.0%} of {p.recent_n}" if p.recent_n else "none"
        md.append(f"| {i} | **{p.ticker}** | BUY {p.side} | {p.pattern} | {p.signal_date} | **{p.success:.0%}** | "
                  f"{p.baseline:.0%} | {own} | {recent} | {p.pooled_win_rate:.0%} | {p.avg_return:+.2%} | {news} | {p.score:.3f} |")
    sized = [p for p in picks if p.plan is not None]
    if sized:
        from .sizing import money

        pl = sized[0].plan
        md += ["", f"## Orders (sized for {money(pl.capital, pl.currency)} capital, {pl.risk_pct:.0%} risk per trade)", ""]
        for i, p in enumerate(sized, 1):
            md.append(f"**{i}. {p.ticker}**  ")
            md += [l.strip() + "  " for l in p.plan.lines()]
            md.append(f"Max loss at stop {money(p.plan.max_loss_at_stop, p.plan.currency)} · if premium goes to zero "
                      f"{money(p.plan.max_loss_total, p.plan.currency)} · premium {p.plan.premium_source} · "
                      f"lot size from {p.plan.lot_source}")
            md.append("")
    md += ["", "Notes:", ""]
    for p in picks:
        extra = "; ".join(p.notes) if p.notes else "none"
        md.append(f"- **{p.ticker}** close {p.last_close:.2f}, trend {p.trend}: {extra}")
    md += ["", "*Success rate* is the share of past signals that made money as the actual trade (buy the option next "
           "morning, intraday stop, Target 1, time exit), blended with the same pattern's rate across the universe "
           "(small samples lean on the universe). *Baseline* is the same trade started on an ordinary day. *Avg move* "
           "is the average return on the option. *Last 3y* repeats the test on recent data only, as a check that "
           "the edge has not faded. *Score* adds up to ±0.10 for news that agrees or disagrees "
           "with the trade. Run `python -m stock_agent trade <TICKER>` for the strike, expiry and breakeven check.",
           "", "_Statistical screen, not investment advice. A 60% setup still loses 4 times in 10._", ""]
    return "\n".join(md)


def validation_line() -> str:
    """One honest sentence on how the strategy did on recent years it was not tuned on."""
    from .tradetest import load_validation

    v = load_validation()
    if not v or v.get("test_avg_ret") is None:
        return ""
    verdict = ("No proven edge: treat these as research ideas, not trade signals."
               if v["test_avg_ret"] <= 0 else "Positive on untouched data, but small: keep risk per trade low.")
    return (f"Strategy check {v['test_from'][:4]}-{v['test_to'][:4]} ({v['test_trades']:,} past trades, not used for tuning): "
            f"{v['test_win_rate']:.0%} won, average {v['test_avg_ret']:+.1%} per option trade. {verdict}")


def render_brief(picks: list[Pick], stats: dict, horizon: int, top: int, universe: str = "") -> str:
    """Short plain-text version for a phone notification or email."""
    as_of = stats.get("as_of") or max((p.signal_date for p in picks), default="today")
    head = f"Top {top} {universe} setups · data to {as_of} · {horizon}-day hold".replace("  ", " ")
    if not picks:
        return head + "\nNo stock has an active setup with a historical edge today. Sit out."
    lines = [head]
    for i, p in enumerate(picks, 1):
        recent = f", last 3y {p.recent_win_rate:.0%}" if p.recent_n else ""
        news = f" · news {p.news_mean:+.2f}" if p.news_count else ""
        lines.append(f"{i}. {p.ticker.split('.')[0]} BUY {p.side} @ {p.last_close:.2f} · {p.pattern} ({p.signal_date[5:]}) · "
                     f"{p.success:.0%} of past trades won (random day {p.baseline:.0%}{recent}), avg {p.avg_return:+.0%} on the option{news}")
        if p.plan is not None:
            lines += p.plan.lines()
        for n in p.notes:
            lines.append(f"   ! {n}")
    plans = [p.plan for p in picks if p.plan is not None]
    if plans:
        pl = plans[0]
        est = any("estimated" in x.premium_source for x in plans)
        from .sizing import money

        lines.append(f"Sized for {money(pl.capital, pl.currency)} capital, {pl.risk_pct:.0%} risk per trade."
                     + (" Premiums are model estimates: check live quotes." if est else ""))
    lines.append(f"Scanned {stats['loaded']} stocks, {stats['candidates']} with an edge. Not investment advice.")
    v = validation_line()
    if v:
        lines.append(v)
    return "\n".join(lines)
