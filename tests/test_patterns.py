"""Unit tests with hand-built candles; run with `python -m unittest`."""
import unittest

import numpy as np
import pandas as pd

from stock_agent import patterns as P
from stock_agent.backtest import evaluate_rules, rank_rules, wilson_lower_bound


def frame(rows):
    """rows: list of (open, high, low, close). Pads 20 dull bars in front for rolling stats."""
    pad = [(100 + i * 0.1, 100.6 + i * 0.1, 99.6 + i * 0.1, 100.3 + i * 0.1) for i in range(20)]
    data = pad + list(rows)
    idx = pd.bdate_range("2024-01-01", periods=len(data))
    df = pd.DataFrame(data, columns=["Open", "High", "Low", "Close"], index=idx)
    df["Volume"] = 1000
    return df


class ShapeTests(unittest.TestCase):
    def parts(self, rows):
        return P.candle_parts(frame(rows))

    def test_hammer(self):
        p = self.parts([(100, 100.2, 95, 100.1)])
        self.assertTrue(P.hammer_shape(p).iloc[-1])
        self.assertFalse(P.inverted_hammer_shape(p).iloc[-1])

    def test_inverted_hammer_and_doji(self):
        p = self.parts([(100, 105, 99.9, 100.1)])
        self.assertTrue(P.inverted_hammer_shape(p).iloc[-1])
        p = self.parts([(100, 101, 99, 100.01)])
        self.assertTrue(P.doji(p).iloc[-1])

    def test_engulfing(self):
        p = self.parts([(101, 101.5, 99.5, 100), (99.8, 102, 99.7, 101.5)])
        self.assertTrue(P.bullish_engulfing(p).iloc[-1])
        p = self.parts([(100, 101.5, 99.5, 101), (101.2, 101.6, 99, 99.5)])
        self.assertTrue(P.bearish_engulfing(p).iloc[-1])

    def test_morning_star(self):
        rows = [(104, 104.2, 100, 100.2), (100, 100.3, 99.7, 99.9), (100.1, 103.5, 100, 103.2)]
        p = self.parts(rows)
        self.assertTrue(P.morning_star(p).iloc[-1])
        self.assertFalse(P.evening_star(p).iloc[-1])

    def test_three_white_soldiers(self):
        rows = [(100, 101.1, 99.9, 101), (100.5, 102.1, 100.4, 102), (101.5, 103.1, 101.4, 103)]
        p = self.parts(rows)
        self.assertTrue(P.three_white_soldiers(p).iloc[-1])

    def test_context_filters_pattern(self):
        # hammer shape but the market was rising, so "Hammer" (needs downtrend) must not fire
        rows = [(100 + i, 100.6 + i, 99.6 + i, 100.5 + i) for i in range(10)] + [(111, 111.2, 106, 111.1)]
        sig = P.detect_all(frame(rows))
        self.assertFalse(sig["Hammer"].iloc[-1])
        self.assertTrue(sig["Hanging Man"].iloc[-1])


class BacktestTests(unittest.TestCase):
    def test_wilson(self):
        self.assertAlmostEqual(wilson_lower_bound(0, 0), 0.0)
        self.assertLess(wilson_lower_bound(3, 3), wilson_lower_bound(60, 100))

    def test_evaluate_and_rank(self):
        rng = np.random.default_rng(0)
        n = 600
        close = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
        open_ = close * (1 + rng.normal(0, 0.005, n))
        high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.005, n)))
        low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.005, n)))
        df = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": 1},
                          index=pd.bdate_range("2020-01-01", periods=n))
        rules = evaluate_rules(df, horizons=(1, 5))
        self.assertEqual(len(rules), len(P.PATTERNS) * 2)
        self.assertTrue(((rules["win_rate"].dropna() >= 0) & (rules["win_rate"].dropna() <= 1)).all())
        ranked = rank_rules(rules, min_samples=5)
        self.assertTrue((ranked["n"] >= 5).all())
        self.assertTrue(ranked["score"].is_monotonic_decreasing)


if __name__ == "__main__":
    unittest.main()
