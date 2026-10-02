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
