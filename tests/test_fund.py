import unittest

import numpy as np
import pandas as pd

from stock_agent import fund


def _universe(n=40, days=900, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=days)
    drift = rng.normal(0.0004, 0.0004, n)
    vol = rng.uniform(0.01, 0.03, n)
    closes = pd.DataFrame(100 * np.cumprod(1 + rng.normal(drift, vol, (days, n)), axis=0), index=idx,
                          columns=[f"S{i}.NS" for i in range(n)])
    values = closes * 1e6
    bench = closes.mean(axis=1)
    sectors = {t: f"Ind{i % 4}" for i, t in enumerate(closes.columns)}
    return closes, values, bench, sectors


class FundTests(unittest.TestCase):
    def test_scores_use_only_past_data(self):
        closes, values, _, _ = _universe()
        rules = fund.FundRules(size=10)
        d = closes.index[500]
        a = fund.scores(closes, values, d, rules)
        b = fund.scores(closes.loc[:d], values.loc[:d], d, rules)
        pd.testing.assert_frame_equal(a, b)

    def test_select_respects_industry_cap(self):
        closes, values, _, sectors = _universe()
        rules = fund.FundRules(size=12, per_industry=3)
        picks = fund.select(fund.scores(closes, values, closes.index[-1], rules), sectors, rules)
        self.assertEqual(len(picks), 12)
        counts = pd.Series([sectors[t] for t in picks]).value_counts()
        self.assertLessEqual(counts.max(), 3)

    def test_rebalance_periods(self):
        idx = pd.bdate_range("2020-01-01", "2022-12-31")
        counts = {f: len(fund._rebalance_dates(idx, f)) for f in ("M", "Q", "H", "Y")}
        self.assertEqual(counts, {"M": 36, "Q": 12, "H": 6, "Y": 3})
        self.assertEqual(str(fund._rebalance_dates(idx, "H")[0].date()), "2020-06-30")

    def test_profit_breakdown_adds_up(self):
        closes, values, bench, sectors = _universe(days=1300)
        for reb in ("M", "Y"):
            b = fund.backtest(closes, values, bench, sectors, fund.FundRules(size=10, rebalance=reb))["breakdown"]
            self.assertAlmostEqual(b["net_profit"] + b["taxes"] + b["costs"] + b["compounding_lost"], b["gross_profit"], places=6)
            self.assertGreater(b["costs"], 0)
            self.assertGreater(b["mf_expenses"], 0)
        m = fund.backtest(closes, values, bench, sectors, fund.FundRules(size=10, rebalance="M"))
        y = fund.backtest(closes, values, bench, sectors, fund.FundRules(size=10, rebalance="Y"))
        self.assertGreater(m["turnover_per_year"], y["turnover_per_year"])   # rebalancing less often trades less
        self.assertGreater(m["breakdown"]["costs"], y["breakdown"]["costs"])

    def test_backtest_shapes_and_tax(self):
        closes, values, bench, sectors = _universe()
        r = fund.backtest(closes, values, bench, sectors, fund.FundRules(size=10, rebalance="Q"))
        c = r["curve"]
        self.assertEqual(list(c.columns), ["fund", "equal_weight", "nifty", "fund_after_tax"])
        self.assertTrue((c["fund_after_tax"] <= c["fund"] + 1e-9).all())   # tax and costs can only take money away
        self.assertGreater(r["turnover_per_year"], 0)
        self.assertIn("fund_cagr", r["after_tax"])
        years = [y["year"] for y in r["yearly"]]
        self.assertEqual(years, sorted(years))
        self.assertGreaterEqual(len(years), c.index.year.nunique() - 1)       # only a one-point stub year is dropped
        self.assertTrue(r["yearly"][-1]["partial"])


if __name__ == "__main__":
    unittest.main()


class MyScreenTests(unittest.TestCase):
    def test_screen_today_and_backtest(self):
        from stock_agent import myscreen as M

        closes, values, bench, sectors = _universe(days=1300)
        sc = M.Screen(filters=[{"metric": "vs_sma200", "op": ">", "value": 0}], rank_by="ret_6m", top=5, rebalance="Q")
        r = M.run(closes, values, bench, sc, sectors=sectors)
        self.assertLessEqual(len(r["picks"]), 5)
        for p in r["picks"]:
            self.assertGreater(p["vs_sma200"], 0)
        six = [p["ret_6m"] for p in r["picks"]]
        self.assertEqual(six, sorted(six, reverse=True))
        bt = r["backtest"]
        self.assertTrue(0 <= bt["hit_rate"] <= 1)
        self.assertIn("screen", bt["metrics"])
        self.assertLess(bt["metrics"]["screen"]["max_drawdown"], 0)
        with self.assertRaises(ValueError):
            M.Screen(filters=[{"metric": "nope", "op": ">", "value": 1}]).validate()

    def test_metrics_do_not_look_ahead(self):
        from stock_agent import myscreen as M

        closes, values, bench, _ = _universe(days=600)
        d = closes.index[450]
        a = M.snapshot(M.metric_frames(closes, values, bench), d)
        b = M.snapshot(M.metric_frames(closes.loc[:d], values.loc[:d], bench.loc[:d]), d)
        pd.testing.assert_frame_equal(a, b)
