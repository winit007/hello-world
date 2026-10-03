"""Check my trade: replaying a described trade over history. Synthetic prices, no network."""
import unittest

import numpy as np
import pandas as pd

from stock_agent import tradecheck as T


def prices(drift=0.0, days=1500, vol=0.012, seed=3, freq="B"):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2018-01-01", periods=days, freq=freq)
    c = 100 * np.exp(np.cumsum(drift + rng.normal(0, vol, days)))
    o = c * (1 + rng.normal(0, vol / 4, days))
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * (1 + abs(rng.normal(0, vol / 2, days))),
                         "Low": np.minimum(o, c) * (1 - abs(rng.normal(0, vol / 2, days))), "Close": c, "Volume": 1e5}, index=idx)


class TradeCheckTests(unittest.TestCase):
    def test_uptrend_favours_buying_and_symmetry_without_drift(self):
        up = prices(drift=0.002)
        e = float(up["Close"].iloc[-1])
        buy = T.check(up, "stock", "buy", e, e * 0.97, e * 1.03, 10)
        sell = T.check(up, "stock", "sell", e, e * 1.03, e * 0.97, 10, product="intraday")
        self.assertGreater(buy["all"]["win_rate"], sell["all"]["win_rate"] + 0.2)
        flat = prices(drift=0.0, seed=8)
        e = float(flat["Close"].iloc[-1])
        b = T.check(flat, "stock", "buy", e, e * 0.97, e * 1.03, 10, cost=0.0)
        # no drift: target and stop are reached about equally often; the stop-first tie rule leans conservative
        self.assertLess(abs(b["all"]["target_first"] - b["all"]["stop_first"]), 0.15)
        self.assertGreaterEqual(b["all"]["stop_first"], b["all"]["target_first"] - 0.03)

    def test_breakeven_and_outcome_shares(self):
        df = prices()
        e = float(df["Close"].iloc[-1])
        r = T.check(df, "stock", "buy", e, e * 0.98, e * 1.04, 5)
        self.assertAlmostEqual(r["breakeven"], 1 / 3, places=3)            # risk 2 to make 4
        a = r["all"]
        self.assertAlmostEqual(a["target_first"] + a["stop_first"] + a["time_exit"], 1.0, places=6)
        self.assertTrue(a["low"] <= a["win_rate"] <= a["high"])
        self.assertIn(r["today"]["trend"], ("up", "down"))
        self.assertLessEqual(r["like_today"]["trades"], a["trades"])

    def test_wrong_side_levels_are_rejected(self):
        df = prices()
        e = float(df["Close"].iloc[-1])
        with self.assertRaises(ValueError):
            T.check(df, "stock", "buy", e, e * 1.02, e * 1.05, 5)          # stop above entry for a buy
        with self.assertRaises(ValueError):
            T.check(df, "option", "buy", e, e * 0.98, e * 0.95, 5,           # bought put: stop must be above
                    option={"type": "PE", "strike": e, "days_to_expiry": 25})

    def test_options_bought_and_sold(self):
        df = prices(drift=0.0015)
        e = float(df["Close"].iloc[-1])
        opt = {"type": "CE", "strike": e, "days_to_expiry": 25}
        bought = T.check(df, "option", "buy", e, e * 0.97, e * 1.04, 5, option=opt)
        written = T.check(df, "option", "sell", e, e * 1.04, e * 0.97, 5, option=opt)
        self.assertIsNone(bought["breakeven"])
        self.assertTrue(bought["all"]["trades"] > 500 and written["all"]["trades"] > 500)
        self.assertNotAlmostEqual(bought["all"]["win_rate"], written["all"]["win_rate"], places=2)
        put = T.check(df, "option", "buy", e, e * 1.03, e * 0.96, 5, option={**opt, "type": "PE"})
        self.assertTrue(put["bearish"])
        with self.assertRaises(ValueError):
            T.check(df, "option", "buy", e, None, None, 5, option={"type": "CE"})

    def test_intraday_candles(self):
        df = prices(days=1000, vol=0.003, freq="15min")             # about what Yahoo gives: 60 days
        e = float(df["Close"].iloc[-1])
        r = T.check(df, "commodity", "sell", e, e * 1.004, e * 0.994, 8, product="intraday", interval="15m")
        self.assertEqual(r["interval"], "15m")
        self.assertGreater(r["all"]["trades"], 850)
        self.assertAlmostEqual(r["breakeven"], 0.4, places=3)


if __name__ == "__main__":
    unittest.main()
