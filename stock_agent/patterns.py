"""Candlestick pattern detectors implemented with plain pandas/numpy (no TA-Lib needed).

Every detector returns a boolean Series aligned to the input frame; True marks the bar on
which the pattern *completes*. Patterns carry the trend context in which classical
charting says they matter (a hammer only counts after a decline, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

BULLISH = "bullish"
BEARISH = "bearish"


@dataclass(frozen=True)
class Pattern:
    name: str
    direction: str            # BULLISH or BEARISH
    candles: int              # number of bars the pattern spans
    context: str | None       # "down" / "up" trend required before the pattern, or None
    detect: Callable[[dict], pd.Series]
    description: str = ""


def candle_parts(df: pd.DataFrame, trend_window: int = 20) -> dict:
    """Pre-compute the geometry every detector needs."""
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    body = (c - o).abs()
    rng = (h - l)
    rng_safe = rng.replace(0, np.nan)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    avg_body = body.rolling(14, min_periods=5).mean()
    avg_rng = rng.rolling(14, min_periods=5).mean()
    sma = c.rolling(trend_window, min_periods=trend_window // 2).mean()
    return {
        "o": o, "h": h, "l": l, "c": c,
        "body": body, "rng": rng, "rng_safe": rng_safe,
        "upper": upper, "lower": lower,
        "avg_body": avg_body, "avg_rng": avg_rng,
        "bull": c > o, "bear": c < o,
        "long": body > avg_body,                # bigger than the recent average body
        "short": body < 0.5 * avg_body,
        "trend_down": c < sma,                  # bar closes below its moving average
        "trend_up": c > sma,
        "sma": sma,
    }


def _sh(s: pd.Series, n: int) -> pd.Series:
    return s.shift(n)


# --------------------------------------------------------------------------- single candle
def doji(p):
    return p["body"] <= 0.1 * p["rng_safe"]


def dragonfly_doji(p):
    return doji(p) & (p["lower"] >= 0.6 * p["rng_safe"]) & (p["upper"] <= 0.1 * p["rng_safe"])


def gravestone_doji(p):
    return doji(p) & (p["upper"] >= 0.6 * p["rng_safe"]) & (p["lower"] <= 0.1 * p["rng_safe"])


def hammer_shape(p):
    return (
        (p["body"] > 0)
        & (p["lower"] >= 2 * p["body"])
        & (p["upper"] <= 0.1 * p["rng_safe"])
        & (p["body"] <= 0.35 * p["rng_safe"])
    )


def inverted_hammer_shape(p):
    return (
        (p["body"] > 0)
        & (p["upper"] >= 2 * p["body"])
        & (p["lower"] <= 0.1 * p["rng_safe"])
        & (p["body"] <= 0.35 * p["rng_safe"])
    )


def bullish_marubozu(p):
    return p["bull"] & (p["body"] >= 0.9 * p["rng_safe"]) & p["long"]


def bearish_marubozu(p):
    return p["bear"] & (p["body"] >= 0.9 * p["rng_safe"]) & p["long"]


# --------------------------------------------------------------------------- two candles
def bullish_engulfing(p):
    return (
        _sh(p["bear"], 1) & p["bull"]
        & (p["o"] <= _sh(p["c"], 1)) & (p["c"] >= _sh(p["o"], 1))
        & (p["body"] > _sh(p["body"], 1))
    )


def bearish_engulfing(p):
    return (
        _sh(p["bull"], 1) & p["bear"]
        & (p["o"] >= _sh(p["c"], 1)) & (p["c"] <= _sh(p["o"], 1))
        & (p["body"] > _sh(p["body"], 1))
    )


def bullish_harami(p):
    return (
        _sh(p["bear"] & p["long"], 1)
        & (np.maximum(p["o"], p["c"]) <= _sh(p["o"], 1))
        & (np.minimum(p["o"], p["c"]) >= _sh(p["c"], 1))
        & (p["body"] < 0.6 * _sh(p["body"], 1))
    )


def bearish_harami(p):
    return (
        _sh(p["bull"] & p["long"], 1)
        & (np.maximum(p["o"], p["c"]) <= _sh(p["c"], 1))
        & (np.minimum(p["o"], p["c"]) >= _sh(p["o"], 1))
        & (p["body"] < 0.6 * _sh(p["body"], 1))
    )


def piercing_line(p):
    mid = _sh((p["o"] + p["c"]) / 2, 1)
    return (
        _sh(p["bear"] & p["long"], 1) & p["bull"]
        & (p["o"] < _sh(p["c"], 1)) & (p["c"] > mid) & (p["c"] < _sh(p["o"], 1))
    )


def dark_cloud_cover(p):
    mid = _sh((p["o"] + p["c"]) / 2, 1)
    return (
        _sh(p["bull"] & p["long"], 1) & p["bear"]
        & (p["o"] > _sh(p["c"], 1)) & (p["c"] < mid) & (p["c"] > _sh(p["o"], 1))
    )


def tweezer_bottom(p):
    tol = 0.1 * p["avg_rng"]
    return _sh(p["bear"], 1) & p["bull"] & ((p["l"] - _sh(p["l"], 1)).abs() <= tol)


def tweezer_top(p):
    tol = 0.1 * p["avg_rng"]
    return _sh(p["bull"], 1) & p["bear"] & ((p["h"] - _sh(p["h"], 1)).abs() <= tol)


# --------------------------------------------------------------------------- three candles
def morning_star(p):
    first_mid = _sh((p["o"] + p["c"]) / 2, 2)
    return (
        _sh(p["bear"] & p["long"], 2)
        & (_sh(p["body"], 1) < 0.3 * _sh(p["body"], 2))
        & p["bull"] & (p["c"] > first_mid)
    )


def evening_star(p):
    first_mid = _sh((p["o"] + p["c"]) / 2, 2)
    return (
        _sh(p["bull"] & p["long"], 2)
        & (_sh(p["body"], 1) < 0.3 * _sh(p["body"], 2))
        & p["bear"] & (p["c"] < first_mid)
    )


def three_white_soldiers(p):
    cond = p["bull"] & _sh(p["bull"], 1) & _sh(p["bull"], 2)
    cond &= (p["c"] > _sh(p["c"], 1)) & (_sh(p["c"], 1) > _sh(p["c"], 2))
    cond &= (p["o"] > _sh(p["o"], 1)) & (p["o"] < _sh(p["c"], 1))          # opens inside prior body
    cond &= (_sh(p["o"], 1) > _sh(p["o"], 2)) & (_sh(p["o"], 1) < _sh(p["c"], 2))
    cond &= (p["body"] > 0.6 * p["avg_body"]) & (_sh(p["body"], 1) > 0.6 * p["avg_body"])
    cond &= p["upper"] <= 0.3 * p["body"]
    return cond


def three_black_crows(p):
    cond = p["bear"] & _sh(p["bear"], 1) & _sh(p["bear"], 2)
    cond &= (p["c"] < _sh(p["c"], 1)) & (_sh(p["c"], 1) < _sh(p["c"], 2))
    cond &= (p["o"] < _sh(p["o"], 1)) & (p["o"] > _sh(p["c"], 1))
    cond &= (_sh(p["o"], 1) < _sh(p["o"], 2)) & (_sh(p["o"], 1) > _sh(p["c"], 2))
    cond &= (p["body"] > 0.6 * p["avg_body"]) & (_sh(p["body"], 1) > 0.6 * p["avg_body"])
    cond &= p["lower"] <= 0.3 * p["body"]
    return cond


PATTERNS: list[Pattern] = [
    Pattern("Hammer", BULLISH, 1, "down", hammer_shape, "Small body, long lower wick after a decline"),
    Pattern("Inverted Hammer", BULLISH, 1, "down", inverted_hammer_shape, "Small body, long upper wick after a decline"),
    Pattern("Hanging Man", BEARISH, 1, "up", hammer_shape, "Hammer shape after an advance"),
    Pattern("Shooting Star", BEARISH, 1, "up", inverted_hammer_shape, "Inverted hammer shape after an advance"),
    Pattern("Doji after downtrend", BULLISH, 1, "down", doji, "Indecision candle after a decline"),
    Pattern("Doji after uptrend", BEARISH, 1, "up", doji, "Indecision candle after an advance"),
    Pattern("Dragonfly Doji", BULLISH, 1, "down", dragonfly_doji, "Doji with long lower wick"),
    Pattern("Gravestone Doji", BEARISH, 1, "up", gravestone_doji, "Doji with long upper wick"),
    Pattern("Bullish Marubozu", BULLISH, 1, None, bullish_marubozu, "Full-body up candle, no wicks"),
    Pattern("Bearish Marubozu", BEARISH, 1, None, bearish_marubozu, "Full-body down candle, no wicks"),
    Pattern("Bullish Engulfing", BULLISH, 2, "down", bullish_engulfing, "Up body swallows prior down body"),
    Pattern("Bearish Engulfing", BEARISH, 2, "up", bearish_engulfing, "Down body swallows prior up body"),
    Pattern("Bullish Harami", BULLISH, 2, "down", bullish_harami, "Small body inside prior large down body"),
    Pattern("Bearish Harami", BEARISH, 2, "up", bearish_harami, "Small body inside prior large up body"),
    Pattern("Piercing Line", BULLISH, 2, "down", piercing_line, "Gap down, close above prior midpoint"),
    Pattern("Dark Cloud Cover", BEARISH, 2, "up", dark_cloud_cover, "Gap up, close below prior midpoint"),
    Pattern("Tweezer Bottom", BULLISH, 2, "down", tweezer_bottom, "Two bars sharing the same low"),
    Pattern("Tweezer Top", BEARISH, 2, "up", tweezer_top, "Two bars sharing the same high"),
    Pattern("Morning Star", BULLISH, 3, "down", morning_star, "Long down, small body, long up closing above midpoint"),
    Pattern("Evening Star", BEARISH, 3, "up", evening_star, "Long up, small body, long down closing below midpoint"),
    Pattern("Three White Soldiers", BULLISH, 3, None, three_white_soldiers, "Three rising full-body up candles"),
    Pattern("Three Black Crows", BEARISH, 3, None, three_black_crows, "Three falling full-body down candles"),
]


def detect_all(df: pd.DataFrame, patterns: list[Pattern] = PATTERNS, use_context: bool = True) -> pd.DataFrame:
    """Boolean frame: one column per pattern, True where the pattern completes."""
    p = candle_parts(df)
    out = {}
    for pat in patterns:
        sig = pat.detect(p).fillna(False).astype(bool)
        if use_context and pat.context:
            # the trend is judged on the bar *before* the pattern starts
            trend = p["trend_down"] if pat.context == "down" else p["trend_up"]
            sig &= trend.shift(pat.candles).fillna(False).astype(bool)
        out[pat.name] = sig
    return pd.DataFrame(out, index=df.index)


PATTERN_BY_NAME = {p.name: p for p in PATTERNS}
