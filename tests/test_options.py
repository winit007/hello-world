import unittest
from datetime import date

import numpy as np
import pandas as pd

from stock_agent import options as O
from stock_agent.report import Outlook


def _df(n=300, drift=0.0):
    rng = np.random.default_rng(1)
    close = 100 * np.cumprod(1 + rng.normal(drift, 0.01, n))
    o = close * (1 + rng.normal(0, 0.003, n))
    df = pd.DataFrame({"Open": o, "High": np.maximum(o, close) * 1.004, "Low": np.minimum(o, close) * 0.996,
                       "Close": close, "Volume": 1}, index=pd.bdate_range("2024-01-01", periods=n))
    return df


def _chain(spot):
    strikes = [spot - 10, spot - 5, spot, spot + 5, spot + 10]
    rows = lambda: [{"strike": k, "lastPrice": 3.0, "bid": 2.9, "ask": 3.1, "impliedVolatility": 0.0, "openInterest": 1, "volume": 1} for k in strikes]
    return {"expiries": {"2025-03-07": {"calls": rows(), "puts": rows()}, "2025-03-21": {"calls": rows(), "puts": rows()}}}


def _outlook(df, bias, conf):
    return Outlook("TST", df.index[-1], float(df["Close"].iloc[-1]), "up", bias, conf, 5, None, None, 0.2, 0.1,
                   signals=[], reasons=["r1"])


class PricingTests(unittest.TestCase):
    def test_bs_iv_roundtrip(self):
        p = O.bs_price(100, 100, 0.1, 0.3, "call")
        self.assertAlmostEqual(O.implied_vol(p, 100, 100, 0.1, "call"), 0.3, places=3)
        self.assertGreater(O.bs_price(100, 100, 0.1, 0.3, "put"), 0)

    def test_choose_expiry(self):
        exps = ["2025-03-03", "2025-03-07", "2025-03-21"]
        # 5 bars ≈ 7 calendar days + 3 buffer → need >= Mar 11
        self.assertEqual(O.choose_expiry(exps, date(2025, 3, 1), 5), "2025-03-21")
        self.assertIsNone(O.choose_expiry(exps, date(2025, 4, 1), 5))


class RecommendTests(unittest.TestCase):
    def test_neutral_is_no_trade(self):
        df = _df()
        t = O.recommend(_outlook(df, "neutral", 0.5), df, _chain(100))
        self.assertEqual(t.action, "NO TRADE")

    def test_bullish_gives_call_when_history_pays(self):
        df = _df(drift=0.004)  # strong uptrend, cheap premium → call should pay historically
        spot = float(df["Close"].iloc[-1])
        out = _outlook(df, "bullish", 0.5)
        out.as_of = pd.Timestamp("2025-02-20")
        t = O.recommend(out, df, _chain(round(spot)))
        self.assertEqual(t.action, "BUY CALL")
        self.assertEqual(t.expiry, "2025-03-07")
        self.assertAlmostEqual(t.premium, 3.0)
        self.assertGreater(t.exp_pnl_contract, 0)

    def test_bearish_without_chain_is_direction_only(self):
        df = _df()
        t = O.recommend(_outlook(df, "bearish", 0.5), df, {})
        self.assertEqual(t.action, "BUY PUT")
        self.assertIsNone(t.expiry)
        self.assertTrue(t.warnings)


if __name__ == "__main__":
    unittest.main()
