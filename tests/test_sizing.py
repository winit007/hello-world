import unittest
from datetime import date

import numpy as np
import pandas as pd

from stock_agent import lots, sizing


def _df(n=300):
    rng = np.random.default_rng(3)
    close = 1000 * np.cumprod(1 + rng.normal(0, 0.012, n))
    o = close * (1 + rng.normal(0, 0.004, n))
    return pd.DataFrame({"Open": o, "High": np.maximum(o, close) * 1.006, "Low": np.minimum(o, close) * 0.994,
                         "Close": close, "Volume": 1.0}, index=pd.bdate_range("2025-06-02", periods=n))


class SizingTests(unittest.TestCase):
    def test_nse_expiry_is_last_tuesday(self):
        self.assertEqual(sizing.nse_monthly_expiry(date(2026, 9, 29), 10), date(2026, 10, 27))
        self.assertEqual(sizing.nse_monthly_expiry(date(2026, 10, 1), 10), date(2026, 10, 27))
        self.assertEqual(sizing._next_us_monthly(date(2026, 9, 29), 10), date(2026, 10, 16))

    def test_money(self):
        self.assertEqual(sizing.money(500000, "₹"), "₹5,00,000")
        self.assertEqual(sizing.money(19635, "₹"), "₹19,635")
        self.assertEqual(sizing.money(950, "₹"), "₹950")
        self.assertEqual(sizing.money(12345678, "₹"), "₹1,23,45,678")
        self.assertEqual(sizing.money(25000, "$"), "$25,000")

    def test_snapshot_lots(self):
        parsed = lots._parse(lots.SNAPSHOT.read_text())
        self.assertIn("RELIANCE", parsed)
        self.assertTrue(all(v > 0 for v in parsed["RELIANCE"].values()))

    def test_plan_call_levels_and_budget(self):
        df = _df()
        p = sizing.plan_trade("TEST.NS", df, "bullish", 5, "Hammer", str(df.index[-1].date()),
                              capital=1_000_000, risk_pct=0.02, lot_override=100)
        self.assertEqual(p.side, "CALL")
        self.assertLess(p.stop_underlying, p.entry)
        self.assertLess(p.entry, p.target1_underlying)
        self.assertLess(p.target1_underlying, p.target2_underlying)
        self.assertLess(p.stop_premium, p.premium)
        self.assertLess(p.premium, p.target1_premium)
        self.assertLessEqual(p.max_loss_at_stop, 1_000_000 * 0.02 + 1e-6)
        self.assertLessEqual(p.cost, 1_000_000 * 0.25 + 1e-6)
        self.assertEqual(p.cost, p.premium * 100 * p.lots)

    def test_plan_put_and_zero_lots(self):
        df = _df()
        p = sizing.plan_trade("TEST.NS", df, "bearish", 5, None, None, capital=1_000, lot_override=500)
        self.assertEqual(p.side, "PUT")
        self.assertGreater(p.stop_underlying, p.entry)
        self.assertGreater(p.entry, p.target1_underlying)
        self.assertEqual(p.lots, 0)
        self.assertIn("0 lots", p.lines()[0])


if __name__ == "__main__":
    unittest.main()
