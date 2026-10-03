"""Long-term picks on synthetic prices: ranking by chance of a 12-month gain, sizing, history. No network."""
import unittest

import numpy as np
import pandas as pd

from stock_agent import longterm


def synthetic(n_stocks=30, days=1300, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-01", periods=days)
    drift = np.linspace(-0.0002, 0.0012, n_stocks)            # some stocks trend up, some drift down
    cols = {}
    for k in range(n_stocks):
        r = drift[k] + rng.normal(0, 0.015, days)
        cols[f"S{k:02d}.NS"] = 100 * np.exp(np.cumsum(r))
    closes = pd.DataFrame(cols, index=idx)
    values = closes * 1e6                                      # liquid enough for the fund's turnover rule
    bench = pd.Series(100 * np.exp(np.cumsum(0.0004 + rng.normal(0, 0.01, days))), index=idx)
    sectors = {t: f"Ind{k % 6}" for k, t in enumerate(closes)}
    return closes, values, bench, sectors


class LongTermTests(unittest.TestCase):
    def setUp(self):
        self.data = synthetic()
        self.res = longterm.run(200_000, 10, data=self.data)

    def test_ten_picks_ranked_by_chance(self):
        picks = self.res["picks"]
        self.assertEqual(len(picks), 10)
        chances = [p["win_chance"] for p in picks]
        self.assertEqual(chances, sorted(chances, reverse=True))
        for p in picks:
            self.assertAlmostEqual(p["win_chance"], np.mean([p["band_win"], p["own_win"]]))

    def test_sizing_shares_the_capital_equally(self):
        slot = self.res["slot"]
        self.assertAlmostEqual(slot, 20_000)
        for p in self.res["picks"]:
            self.assertEqual(p["shares"], int(slot // p["price"]))
            self.assertLessEqual(p["amount"], slot)

    def test_history_is_out_of_sample_and_covers_bands(self):
        h = self.res["history"]
        self.assertGreater(h["months"], 20)
        self.assertLess(h["to"], self.res["as_of"])          # every replayed month has a full year after it
        self.assertIsNotNone(h["bands"]["1-5"])
        self.assertTrue(0 <= h["all"]["win"] <= 1)
        self.assertIn("Nifty 50 index fund", longterm.render_text(self.res))

    def test_own_record(self):
        idx = pd.bdate_range("2020-01-01", periods=800)
        up = pd.Series(np.linspace(100, 200, 800), index=idx)
        self.assertEqual(longterm.own_record(up)[0], 1.0)
        self.assertEqual(longterm.own_record(-up + 400)[0], 0.0)
        self.assertIsNone(longterm.own_record(up.iloc[:300])[0])

    def test_next_review_is_the_following_quarter_end(self):
        from datetime import date
        self.assertEqual(longterm._next_review(date(2026, 10, 3)), date(2026, 12, 31))
        self.assertEqual(longterm._next_review(date(2026, 2, 10)), date(2026, 3, 31))
        self.assertEqual(longterm._next_review(date(2026, 3, 20)), date(2026, 6, 30))   # quarter ends too soon


if __name__ == "__main__":
    unittest.main()
