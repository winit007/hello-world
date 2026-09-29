"""Backtest every candlestick pattern on a price history and rank the resulting rules.

A *rule* is "pattern X completed -> trade in its direction and hold H bars". For every rule we
measure the win rate (the user's "conversion ratio"), how far that is above the unconditional
baseline, the average return, the profit factor and a Wilson lower bound that penalises rules
with few samples. Ranking uses the Wilson bound so a 3-for-3 pattern never outranks a 60% pattern
seen 80 times.
"""
from __future__ import annotations

from math import sqrt

import numpy as np
import pandas as pd

from .patterns import PATTERNS, BEARISH, Pattern, detect_all

DEFAULT_HORIZONS = (1, 3, 5, 10)


def wilson_lower_bound(wins: int, n: int, z: float = 1.96) -> float:
    """Lower bound of the 95% confidence interval of a binomial proportion."""
    if n == 0:
        return 0.0
    phat = wins / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    spread = z * sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return (centre - spread) / denom


def forward_returns(close: np.ndarray, idx: np.ndarray, horizon: int) -> np.ndarray:
    valid = idx[idx + horizon < len(close)]
    if len(valid) == 0:
        return np.array([])
    return close[valid + horizon] / close[valid] - 1.0


def baseline_win_rates(close: np.ndarray, horizons) -> dict[tuple[str, int], float]:
    """Unconditional probability that price is up (or down) after H bars."""
    out = {}
    for h in horizons:
        r = close[h:] / close[:-h] - 1.0
        out[("bullish", h)] = float((r > 0).mean())
        out[("bearish", h)] = float((r < 0).mean())
    return out


def evaluate_rules(
    df: pd.DataFrame,
    patterns: list[Pattern] = PATTERNS,
    horizons=DEFAULT_HORIZONS,
    use_context: bool = True,
) -> pd.DataFrame:
    """One row per (pattern, horizon)."""
    close = df["Close"].to_numpy()
    signals = detect_all(df, patterns, use_context=use_context)
    base = baseline_win_rates(close, horizons)
    rows = []
    for pat in patterns:
        idx = np.flatnonzero(signals[pat.name].to_numpy())
        for h in horizons:
            r = forward_returns(close, idx, h)
            if pat.direction == BEARISH:
                r = -r  # directional return: positive means the pattern "worked"
            n = len(r)
            wins = int((r > 0).sum())
            gains = r[r > 0].sum()
            losses = -r[r < 0].sum()
            avg_win = r[r > 0].mean() if wins else 0.0
            avg_loss = -r[r < 0].mean() if (n - wins) else 0.0
            rows.append({
                "pattern": pat.name,
                "direction": pat.direction,
                "horizon": h,
                "n": n,
                "wins": wins,
                "gains": float(gains),
                "losses": float(losses),
                "sum_return": float(r.sum()),
                "win_rate": wins / n if n else np.nan,
                "baseline": base[(pat.direction, h)],
                "edge": (wins / n - base[(pat.direction, h)]) if n else np.nan,
                "avg_return": r.mean() if n else np.nan,
                "median_return": float(np.median(r)) if n else np.nan,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "profit_factor": (gains / losses) if losses > 0 else (np.inf if gains > 0 else np.nan),
                "wilson_lb": wilson_lb(wins, n),
                "p25": float(np.percentile(r, 25)) if n else np.nan,
                "p75": float(np.percentile(r, 75)) if n else np.nan,
                "last_seen": signals.index[idx[-1]] if n else pd.NaT,
            })
    return pd.DataFrame(rows)


def wilson_lb(wins: int, n: int) -> float:  # short alias used above
    return wilson_lower_bound(wins, n)


def pool_rules(per_ticker: dict[str, pd.DataFrame], close_by_ticker: dict[str, np.ndarray] | None = None) -> pd.DataFrame:
    """Merge rule tables from several tickers into one, weighting every metric by sample count.

    A pattern that wins on ten different stocks is far more trustworthy than one that happened
    to work fifteen times on a single name, so this is the best way to find a *general* rule.
    """
    frames = []
    for t, df in per_ticker.items():
        d = df.copy()
        d["ticker"] = t
        d["base_w"] = d["baseline"] * d["n"]
        frames.append(d)
    all_ = pd.concat(frames, ignore_index=True)
    g = all_.groupby(["pattern", "direction", "horizon"], as_index=False).agg(
        n=("n", "sum"), wins=("wins", "sum"), gains=("gains", "sum"), losses=("losses", "sum"),
        sum_return=("sum_return", "sum"), base_w=("base_w", "sum"), tickers=("ticker", "nunique"),
    )
    g["win_rate"] = g["wins"] / g["n"].replace(0, np.nan)
    g["baseline"] = g["base_w"] / g["n"].replace(0, np.nan)
    g["edge"] = g["win_rate"] - g["baseline"]
    g["avg_return"] = g["sum_return"] / g["n"].replace(0, np.nan)
    g["profit_factor"] = np.where(g["losses"] > 0, g["gains"] / g["losses"].replace(0, np.nan), np.inf)
    g["wilson_lb"] = [wilson_lower_bound(int(w), int(n)) for w, n in zip(g["wins"], g["n"])]
    return g.drop(columns=["base_w"])


def rank_rules(rules: pd.DataFrame, min_samples: int = 10) -> pd.DataFrame:
    """Rules with enough samples, best first. Score = Wilson lower bound + a nudge for expectancy."""
    ok = rules[rules["n"] >= min_samples].copy()
    ok["score"] = ok["wilson_lb"] + ok["avg_return"].clip(-0.05, 0.05)  # tiny tie-breaker
    return ok.sort_values(["score", "n"], ascending=False).reset_index(drop=True)


def best_rule_per_pattern(ranked: pd.DataFrame) -> pd.DataFrame:
    return ranked.sort_values("score", ascending=False).drop_duplicates("pattern").reset_index(drop=True)


def active_signals(df: pd.DataFrame, lookback: int = 3, patterns=PATTERNS, use_context=True) -> list[tuple[str, pd.Timestamp]]:
    """Patterns that completed within the last `lookback` bars, newest first."""
    signals = detect_all(df, patterns, use_context=use_context).tail(lookback)
    found = []
    for date, row in signals[::-1].iterrows():
        for name, on in row.items():
            if on:
                found.append((name, date))
    return found
