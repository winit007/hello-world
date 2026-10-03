import unittest

import pandas as pd

from stock_agent import costs as C


class CostTests(unittest.TestCase):
    def test_spread_falls_with_liquidity(self):
        self.assertAlmostEqual(C.spread_rate(1e10), 0.0003)          # ₹1,000 crore a day
        self.assertGreater(C.spread_rate(1e8), C.spread_rate(1e9))
        self.assertEqual(C.spread_rate(1e5), 0.005)                  # capped
        self.assertEqual(C.spread_rate(float("nan")), 0.005)

    def test_small_sells_pay_the_fixed_dp_charge(self):
        small = C.trade_cost(8_000, "sell", 1e10, 0.015)
        big = C.trade_cost(800_000, "sell", 1e10, 0.015)
        self.assertGreater(small["charges"] / 8_000, big["charges"] / 800_000)
        self.assertGreater(small["charges"], C.DP_CHARGE)
        buy = C.trade_cost(8_000, "buy", 1e10, 0.015)
        self.assertLess(buy["charges"], small["charges"])             # buys pay no DP charge
        self.assertGreater(C.trade_cost(1e8, "buy", 1e9, 0.02)["spread_impact"] / 1e8,
                           C.trade_cost(1e5, "buy", 1e9, 0.02)["spread_impact"] / 1e5)   # impact grows with size

    def test_rebalance_cost_counts_both_sides(self):
        adv = pd.Series({"A": 1e9, "B": 1e9, "C": 1e9})
        vol = pd.Series({"A": 0.02, "B": 0.02, "C": 0.02})
        r = C.rebalance_cost({"A": 0.5, "B": 0.5}, {"A": 0.5, "C": 0.5}, 1e6, adv, vol)
        self.assertEqual(r["orders"], 2)
        self.assertAlmostEqual(r["fraction"], (r["charges"] + r["spread_impact"]) / 1e6)
        self.assertEqual(C.rebalance_cost({"A": 1.0}, {"A": 1.0}, 1e6, adv, vol)["orders"], 0)

    def test_band_skips_small_trades(self):
        prev = {"A": 0.22, "B": 0.18, "C": 0.30, "D": 0.30}
        target = {"A": 0.25, "B": 0.25, "C": 0.25, "E": 0.25}
        w = C.with_band(prev, target)
        self.assertAlmostEqual(sum(w.values()), 1.0)
        self.assertEqual(w["A"], 0.22)                 # within 20% of 0.25: left alone
        self.assertEqual(set(w), set(target))
        self.assertAlmostEqual(w["B"], w["E"])         # B (28% off) and C trade, sharing the rest with E
        self.assertEqual(C.with_band({}, target), target)


if __name__ == "__main__":
    unittest.main()
