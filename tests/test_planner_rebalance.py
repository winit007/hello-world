import unittest

from stock_agent import planner
from stock_agent import rebalance as R

STATS = {"equity": {"expected": 0.11, "vol": 0.16, "hist_cagr": 0.115, "years": 10, "proxy": "x"},
         "gold": {"expected": 0.09, "vol": 0.14, "hist_cagr": 0.16, "years": 10, "proxy": "y"},
         "debt": {"expected": 0.07, "vol": 0.02, "hist_cagr": None, "years": None, "proxy": "assumed"},
         "corr_equity_gold": 0.0}


class PlannerTests(unittest.TestCase):
    def test_allocation_rules(self):
        for yrs in (1, 4, 8, 20):
            for prof in planner.PROFILES:
                mix = planner.allocation(yrs, prof)
                self.assertAlmostEqual(sum(mix.values()), 1.0, places=6)
                self.assertTrue(all(v >= 0 for v in mix.values()))
        self.assertLessEqual(planner.allocation(2, "aggressive")["equity"], 0.15)
        self.assertGreater(planner.allocation(15, "aggressive")["equity"], planner.allocation(15, "conservative")["equity"])

    def test_plan_is_consistent(self):
        g = planner.Goal(target_today=1_000_000, years=10, current=0, monthly=5000, step_up=0.05, inflation=0.06)
        p = planner.plan(g, stats=STATS)
        self.assertAlmostEqual(p["target_future"], 1_000_000 * 1.06 ** 10)
        self.assertTrue(0 <= p["probability"] <= 1)
        self.assertLessEqual(p["p10"], p["median"])
        self.assertLessEqual(p["median"], p["p90"])
        self.assertEqual(len(p["bands"]), 11)
        richer = planner.plan(planner.Goal(**{**p["goal"], "monthly": p["needed_monthly_75"]}), stats=STATS)
        self.assertGreaterEqual(richer["probability"], 0.70)   # the suggested SIP really gives about 75%
        more = planner.plan(planner.Goal(**{**p["goal"], "monthly": 50000}), stats=STATS)
        self.assertGreater(more["probability"], p["probability"])


class RebalanceTests(unittest.TestCase):
    def _h(self, c, v, name=None):
        return {"name": name or c, "asset_class": c, "value": v, "price": None, "symbol": None, "quantity": None}

    def test_within_band_no_trades(self):
        rec = R.recommend([self._h("equity", 610), self._h("debt", 290), self._h("gold", 100)],
                          {"equity": 60, "debt": 30, "gold": 10})
        self.assertFalse(rec["needed"])
        self.assertEqual(rec["sells"], [])

    def test_full_rebalance_and_new_money(self):
        hold = [self._h("equity", 600, "A"), self._h("equity", 200, "B"), self._h("debt", 150), self._h("gold", 50)]
        rec = R.recommend(hold, {"equity": 0.6, "debt": 0.3, "gold": 0.1}, band=0.05, new_money=200)
        self.assertTrue(rec["needed"])
        sold = sum(s["amount"] for s in rec["sells"])
        bought = sum(b["amount"] for b in rec["buys"])
        self.assertAlmostEqual(sold, bought, places=1)                 # rebalancing is self-financing
        a = next(s for s in rec["sells"] if s["name"] == "A")["amount"]
        b = next(s for s in rec["sells"] if s["name"] == "B")["amount"]
        self.assertAlmostEqual(a / b, 3.0, places=3)                   # sold in proportion to size
        self.assertAlmostEqual(sum(rec["new_split"].values()), 200, places=0)
        self.assertNotIn("equity", rec["new_split"])                   # new money avoids the overweight class


if __name__ == "__main__":
    unittest.main()
