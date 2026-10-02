import unittest

import numpy as np
import pandas as pd

from stock_agent import lab, sizing, tradetest as T


def _df(n=700, seed=5, drift=0.0):
    rng = np.random.default_rng(seed)
    c = 1000 * np.cumprod(1 + rng.normal(drift, 0.012, n))
    o = c * (1 + rng.normal(0, 0.004, n))
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * (1 + abs(rng.normal(0, 0.005, n))),
                         "Low": np.minimum(o, c) * (1 - abs(rng.normal(0, 0.005, n))), "Close": c, "Volume": 1.0},
                        index=pd.bdate_range("2021-01-04", periods=n))


class TradeTestTests(unittest.TestCase):
    def test_levels_match_order_sizing(self):
        df = _df()
        plan = sizing.plan_trade("TEST.NS", df, "bullish", 5, "Hammer", str(df.index[-1].date()), lot_override=100)
        a = sizing.atr(df)
        stop, t1, t2, _ = T.levels(float(df["Close"].iloc[-1]), float(df["Low"].iloc[-1]), float(df["High"].iloc[-1]),
                                   a, True, T.Strategy())
        self.assertAlmostEqual(plan.stop_underlying, round(stop, 2), places=2)
        self.assertAlmostEqual(plan.target1_underlying, round(t1, 2), places=2)
        self.assertAlmostEqual(plan.target2_underlying, round(t2, 2), places=2)

    def test_outcomes_have_no_lookahead(self):
        df = _df()
        full = T.trade_outcomes(df, "TEST.NS")
        self.assertTrue((full["exit"] > full["signal"]).all())
        cut = df.index[400]
        part = T.trade_outcomes(df[df.index <= cut], "TEST.NS")
        # every trade that had finished by the cut is identical whether or not later data exists
        a = full[full["exit"] <= cut].sort_values(["pattern", "signal"]).reset_index(drop=True)
        b = part.sort_values(["pattern", "signal"]).reset_index(drop=True)
        pd.testing.assert_frame_equal(a[["pattern", "signal", "exit", "skipped"]], b[["pattern", "signal", "exit", "skipped"]])
        np.testing.assert_allclose(a["ret"].to_numpy(), b["ret"].to_numpy())

    def test_stats_baseline_and_filters(self):
        df = _df(drift=0.001)
        st = T.trade_stats(T.trade_outcomes(df, "TEST.NS"))
        self.assertFalse(st["pattern"].str.startswith("_baseline").any())
        self.assertTrue(st["baseline"].between(0, 1).all())
        trend = T.trade_outcomes(df, "TEST.NS", strategy=T.Strategy(trend="sma200"), baseline_step=0)
        allow = pd.Series(T.allowed_mask(df, True, T.Strategy(trend="sma200"), None), index=df.index)
        bull = trend[trend["direction"] == "bullish"]
        self.assertTrue(allow.loc[bull["signal"]].all())

    def test_summary_and_strategy_roundtrip(self):
        o = pd.DataFrame({"signal": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
                          "exit": pd.to_datetime(["2024-01-05", "2024-01-06", "2024-01-07"]),
                          "ret": [0.5, -0.2, -0.1], "r": [1, -1, -0.5], "skipped": [False, False, False]})
        s = lab.summarize(o)
        self.assertEqual(s["trades"], 3)
        self.assertAlmostEqual(s["win_rate"], 1 / 3)
        self.assertAlmostEqual(s["profit_factor"], 0.5 / 0.3)
        self.assertEqual(s["worst_streak"], 2)
        st = T.Strategy(target=2.0, trend="sma200", patterns=("Hammer",))
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            T.save_strategy(st, Path(d) / "s.json")
            self.assertIn('"target": 2.0', (Path(d) / "s.json").read_text())


if __name__ == "__main__":
    unittest.main()
