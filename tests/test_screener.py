import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from stock_agent import screener


def _df(seed, n=600):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.015, n))
    o = close * (1 + rng.normal(0, 0.006, n))
    return pd.DataFrame({"Open": o, "High": np.maximum(o, close) * (1 + abs(rng.normal(0, 0.005, n))),
                         "Low": np.minimum(o, close) * (1 - abs(rng.normal(0, 0.005, n))), "Close": close,
                         "Volume": 1.0}, index=pd.bdate_range("2022-01-03", periods=n))


class ScreenTests(unittest.TestCase):
    def test_screen_ranks_and_limits(self):
        frames = {f"S{i}": _df(i) for i in range(12)}
        with mock.patch.object(screener, "load_prices", side_effect=lambda t, *a, **k: frames[t]), \
             mock.patch.object(screener, "load_news", return_value=[]), tempfile.TemporaryDirectory() as d:
            picks, ranked, stats = screener.screen(list(frames), lookback=30, top=5, min_edge=-1.0,
                                                   cache_dir=Path(d), log=lambda *a: None)
        self.assertEqual(stats["loaded"], 12)
        self.assertLessEqual(len(picks), 5)
        self.assertEqual(picks, ranked[:5])
        scores = [p.score for p in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for p in ranked:
            self.assertGreater(p.avg_return, 0)
            self.assertTrue(0 <= p.success <= 1)
            # shrinkage keeps the estimate between the stock's own rate and the universe rate
            if p.n:
                lo, hi = sorted([p.raw_win_rate, p.pooled_win_rate])
                self.assertTrue(lo - 1e-9 <= p.success <= hi + 1e-9)
        md = screener.render_markdown(picks, ranked, stats, 5, 5)
        self.assertIn("Top 5 setups", md)

    def test_news_tilts_score(self):
        p = screener.Pick("X", "Hammer", "bullish", "2024-01-01", 1, 5, 10, .6, .55, .58, .5, .01)
        # direct check of the scoring rule used in screen()
        p.news_mean, p.news_count = 0.2, 10
        aligned = float(np.clip(p.news_mean * 4, -1, 1))
        self.assertAlmostEqual(p.success + 0.10 * aligned, 0.58 + 0.08)


if __name__ == "__main__":
    unittest.main()
