"""Combine backtest, live signals and news into an outlook and a markdown report."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .backtest import active_signals, best_rule_per_pattern, evaluate_rules, rank_rules
from .news import Headline, summarize
from .patterns import PATTERN_BY_NAME, BULLISH

DISCLAIMER = (
    "Statistical research only, not investment advice. Win rates are measured on this ticker's own "
    "past data and can shrink or vanish in the future. Position size for being wrong."
)


@dataclass
class Outlook:
    ticker: str
    as_of: pd.Timestamp
    last_close: float
    trend: str                       # "up" / "down" / "flat"
    bias: str                        # "bullish" / "bearish" / "neutral"
    confidence: float                # 0..1
    horizon: int
    expected_return: float | None    # avg historical directional return of matched rules
    expected_range: tuple[float, float] | None
    technical_score: float
    news_score: float
    signals: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def trend_state(df: pd.DataFrame) -> tuple[str, str]:
    c = df["Close"]
    sma20 = c.rolling(20).mean().iloc[-1]
    sma50 = c.rolling(50).mean().iloc[-1]
    last = c.iloc[-1]
    if last > sma20 > sma50:
        return "up", f"close {last:.2f} above SMA20 {sma20:.2f} above SMA50 {sma50:.2f}"
    if last < sma20 < sma50:
        return "down", f"close {last:.2f} below SMA20 {sma20:.2f} below SMA50 {sma50:.2f}"
    return "flat", f"close {last:.2f}, SMA20 {sma20:.2f}, SMA50 {sma50:.2f} (mixed)"


def build_outlook(
    ticker: str,
    df: pd.DataFrame,
    rules: pd.DataFrame,
    headlines: list[Headline],
    horizon: int = 5,
    lookback: int = 3,
    min_samples: int = 10,
) -> Outlook:
    ranked = rank_rules(rules, min_samples)
    trend, trend_text = trend_state(df)
    reasons = [f"Trend: {trend} ({trend_text})"]

    # --- technical score from patterns seen in the last few bars, weighted by proven edge
    tech, sigs, exp_rets, lows, highs = 0.0, [], [], [], []
    for name, date in active_signals(df, lookback):
        pat = PATTERN_BY_NAME[name]
        row = ranked[(ranked["pattern"] == name) & (ranked["horizon"] == horizon)]
        sign = 1 if pat.direction == BULLISH else -1
        if row.empty:
            reasons.append(f"{name} on {date.date()} but fewer than {min_samples} past samples; ignored")
            sigs.append({"pattern": name, "date": str(date.date()), "direction": pat.direction, "n": 0})
            continue
        r = row.iloc[0]
        # weight = how much better than coin-flip this rule has been, sample-adjusted
        weight = max(r["wilson_lb"] - 0.5, 0.0) + max(r["edge"], 0.0)
        sigs.append({
            "pattern": name, "date": str(date.date()), "direction": pat.direction, "n": int(r["n"]),
            "win_rate": float(r["win_rate"]), "baseline": float(r["baseline"]), "avg_return": float(r["avg_return"]),
            "edge": float(r["edge"]), "counted": weight > 0,
        })
        stats = (f"historically {r['win_rate']:.0%} win rate over {horizon} bars vs "
                 f"{r['baseline']:.0%} baseline, n={int(r['n'])}")
        if weight <= 0:
            reasons.append(f"{name} ({pat.direction}) on {date.date()}: {stats}; no edge on this ticker, not counted")
            continue
        tech += sign * weight
        # convert the rule's directional return into a *price* return for the expectation line
        exp_rets.append(sign * r["avg_return"])
        lows.append(r["p25"] if sign > 0 else -r["p75"])
        highs.append(r["p75"] if sign > 0 else -r["p25"])
        reasons.append(f"{name} ({pat.direction}) on {date.date()}: {stats}")

    # --- news score
    ns = summarize(headlines)
    news = float(np.clip(ns["mean"] * 2, -1, 1))  # compound mean is typically small; stretch it
    if ns["count"]:
        reasons.append(
            f"News: {ns['count']} headlines, mean sentiment {ns['mean']:+.2f} "
            f"({ns['positive']} positive / {ns['negative']} negative)"
        )
    else:
        reasons.append("News: no headlines available")

    # --- combine: technical evidence dominates, news and trend tilt it
    trend_score = {"up": 0.1, "down": -0.1, "flat": 0.0}[trend]
    total = 0.6 * np.tanh(tech * 4) + 0.25 * news + 0.15 * trend_score / 0.1
    bias = "bullish" if total > 0.15 else "bearish" if total < -0.15 else "neutral"
    confidence = float(min(abs(total), 1.0))

    return Outlook(
        ticker=ticker,
        as_of=df.index[-1],
        last_close=float(df["Close"].iloc[-1]),
        trend=trend,
        bias=bias,
        confidence=confidence,
        horizon=horizon,
        expected_return=float(np.mean(exp_rets)) if exp_rets else None,
        expected_range=(float(np.mean(lows)), float(np.mean(highs))) if lows else None,
        technical_score=float(tech),
        news_score=news,
        signals=sigs,
        reasons=reasons,
    )


def _pct(x) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:+.2%}"


def _rules_table(rules: pd.DataFrame, limit: int | None = None) -> str:
    cols = ["pattern", "direction", "horizon", "n", "win_rate", "baseline", "edge", "avg_return", "profit_factor", "wilson_lb"]
    rows = rules[cols] if limit is None else rules[cols].head(limit)
    lines = ["| Pattern | Dir | Hold | n | Win rate | Baseline | Edge | Avg return | Profit factor | Wilson LB |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in rows.iterrows():
        pf = "inf" if np.isinf(r["profit_factor"]) else f"{r['profit_factor']:.2f}"
        lines.append(
            f"| {r['pattern']} | {r['direction']} | {int(r['horizon'])}d | {int(r['n'])} | {r['win_rate']:.1%} | "
            f"{r['baseline']:.1%} | {r['edge']:+.1%} | {r['avg_return']:+.2%} | {pf} | {r['wilson_lb']:.1%} |"
        )
    return "\n".join(lines)


def render_markdown(
    outlook: Outlook,
    rules: pd.DataFrame,
    headlines: list[Headline],
    min_samples: int = 10,
    narrative: str | None = None,
    top_n: int = 10,
) -> str:
    ranked = rank_rules(rules, min_samples)
    per_pattern = best_rule_per_pattern(ranked)
    ns = summarize(headlines)

    md = [f"# {outlook.ticker} research report", "",
          f"*As of {outlook.as_of.date()} · last close {outlook.last_close:.2f}*", "",
          "## Outlook", "",
          f"**Bias: {outlook.bias.upper()}** (confidence {outlook.confidence:.0%}) over the next {outlook.horizon} trading days.", ""]
    if outlook.expected_return is not None and outlook.expected_range:
        lo, hi = outlook.expected_range
        md.append(f"Price move after the matched rules, historically: {_pct(outlook.expected_return)} on average "
                  f"(middle half of outcomes {_pct(lo)} to {_pct(hi)}).")
        md.append("")
    md += ["Why:", ""] + [f"- {r}" for r in outlook.reasons] + [""]
    if narrative:
        md += ["## Local model briefing", "", narrative, ""]

    md += ["## Best rules for this ticker (highest reliable win rate first)", "",
           f"Only rules with at least {min_samples} historical occurrences are ranked. *Win rate* is the share of "
           f"signals followed by a move in the pattern's direction after the holding period; *Edge* is that minus "
           f"the ticker's unconditional rate; *Wilson LB* is the sample-size-adjusted lower bound used for ranking.", ""]
    if ranked.empty:
        md.append("_Not enough history to rank any rule._")
    else:
        md.append(_rules_table(ranked, top_n))
        top = ranked.iloc[0]
        md += ["", f"**Recommended rule:** when a **{top['pattern']}** completes, trade {top['direction']} and hold "
               f"**{int(top['horizon'])} bars**. Historically {top['win_rate']:.0%} win rate over {int(top['n'])} signals "
               f"({top['edge']:+.0%} vs baseline), average {_pct(top['avg_return'])} per trade."]
    md += ["", "## Best holding period per pattern", ""]
    md.append(_rules_table(per_pattern) if not per_pattern.empty else "_none_")

    md += ["", "## Patterns seen in the last bars", ""]
    if outlook.signals:
        for s in outlook.signals:
            if s["n"]:
                md.append(f"- {s['date']}: **{s['pattern']}** ({s['direction']}) — {s['win_rate']:.0%} win rate "
                          f"({s['edge']:+.0%} vs baseline), n={s['n']}")
            else:
                md.append(f"- {s['date']}: **{s['pattern']}** ({s['direction']}) — too few samples to trust")
    else:
        md.append("_No pattern completed in the lookback window._")

    md += ["", "## News sentiment", ""]
    if ns["count"]:
        md.append(f"{ns['count']} headlines · mean {ns['mean']:+.2f} · {ns['positive']} positive / "
                  f"{ns['neutral']} neutral / {ns['negative']} negative")
        if ns["themes"]:
            md.append("Themes: " + ", ".join(f"{k} ({v})" for k, v in ns["themes"].items()))
        md += ["", "| Date | Sentiment | Headline | Source |", "|---|---|---|---|"]
        for h in headlines[:25]:
            md.append(f"| {h.published} | {h.sentiment:+.2f} {h.label} | [{h.title}]({h.link}) | {h.source} |")
    else:
        md.append("_No headlines available (offline and nothing cached)._")

    md += ["", "---", f"_{DISCLAIMER}_", ""]
    return "\n".join(md)
