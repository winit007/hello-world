"""Robust backtest methods on synthetic data with known answers. No network."""
import unittest

import numpy as np
import pandas as pd

from stock_agent import robust


def synthetic_trades(n=6000, signal_strength=1.5, seed=1):
    """Trades over 2016-2025 whose win chance depends on mom20 when signal_strength > 0."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2016-01-01", "2025-12-31")
    sig = pd.Series(np.sort(rng.choice(days, n)))
    x = {f: rng.normal(0, 1, n) for f in robust.FEATURES}
    p = 1 / (1 + np.exp(-(-0.4 + signal_strength * x["mom20"])))
    win = rng.random(n) < p
    ret = np.where(win, rng.uniform(0.05, 0.4, n), rng.uniform(-0.4, -0.05, n))
    pats = rng.choice(robust.PATTERN_NAMES[:6], n)
    d = pd.DataFrame({"signal": sig, "exit": sig + pd.Timedelta(days=7), "pattern": pats,
                      "direction": np.where(rng.random(n) < 0.5, "bullish", "bearish"), "ret": ret,
                      "win": win.astype(int), **x})
    return d


class RobustTests(unittest.TestCase):
    def test_logistic_recovers_the_signal(self):
        d = synthetic_trades()
        X, mu, sd = robust._design(d)
        w = robust.fit_logistic(X, d["win"].to_numpy(float))
        k = 1 + robust.FEATURES.index("mom20")
        self.assertGreater(w[k], 1.0)                              # strong positive weight on the true feature
        others = [abs(w[1 + i]) for i, f in enumerate(robust.FEATURES) if f != "mom20"]
        self.assertLess(max(others), 0.2)

    def test_walk_forward_meta_filter_helps_when_there_is_signal(self):
        wf = robust.walk_forward(synthetic_trades())
        self.assertGreaterEqual(len(wf["per_year"]), 4)
        self.assertGreater(wf["meta"]["win_rate"], wf["all"]["win_rate"] + 0.1)
        self.assertGreater(wf["meta"]["avg_ret"], wf["all"]["avg_ret"])
        self.assertLess(wf["brier"], wf["brier_base"])            # calibrated better than guessing the average
        for y in wf["per_year"]:                                   # every test year comes after its training data
            self.assertGreaterEqual(y["year"], 2020)

    def test_walk_forward_finds_nothing_in_noise(self):
        wf = robust.walk_forward(synthetic_trades(signal_strength=0.0))
        self.assertLess(abs(wf["meta"]["win_rate"] - wf["all"]["win_rate"]), 0.04)
        self.assertGreaterEqual(wf["brier"], wf["brier_base"] - 0.002)

    def test_purge_drops_trades_that_run_into_the_test_year(self):
        d = synthetic_trades(500)
        start = pd.Timestamp(2020, 1, 1)
        tr = robust._train_window(d, start)
        self.assertTrue((tr["exit"] < start - pd.Timedelta(days=robust.EMBARGO_DAYS)).all())

    def test_pbo_near_half_for_noise_and_fdr_rejects_noise(self):
        d = synthetic_trades(8000, signal_strength=0.0, seed=4)
        res = robust.pbo(d, blocks=10, min_trades=10)
        self.assertIsNotNone(res)
        self.assertTrue(0.2 <= res["pbo"] <= 0.8)
        f = robust.fdr(d, {"bullish": float(d["win"].mean()), "bearish": float(d["win"].mean())})
        self.assertEqual(f["survive"], 0)

    def test_fdr_keeps_a_real_pattern(self):
        d = synthetic_trades(8000, signal_strength=0.0, seed=5)
        good = d["pattern"] == robust.PATTERN_NAMES[0]
        d.loc[good, "win"] = (np.random.default_rng(0).random(good.sum()) < 0.75).astype(int)
        base = float(d.loc[~good, "win"].mean())
        f = robust.fdr(d, {"bullish": base, "bearish": base})
        self.assertGreaterEqual(f["survive"], 1)
        self.assertEqual(f["patterns"][0]["pattern"], robust.PATTERN_NAMES[0])

    def test_bootstrap_range(self):
        d = synthetic_trades(3000, signal_strength=0.0)
        b = robust.bootstrap(d, n=300)
        self.assertLess(b["low"], b["median"])
        self.assertLess(b["median"], b["high"])
        self.assertTrue(0 <= b["p_positive"] <= 1)

    def test_features_do_not_look_ahead(self):
        idx = pd.bdate_range("2020-01-01", periods=400)
        c = pd.Series(100 + np.cumsum(np.random.default_rng(2).normal(0, 1, 400)), index=idx)
        df = pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1000.0})
        full = robust.stock_features(df, None)
        cut = robust.stock_features(df.iloc[:300], None)
        pd.testing.assert_frame_equal(full.iloc[:300], cut)

    def test_score_today(self):
        d = synthetic_trades(2000)
        X, mu, sd = robust._design(d)
        w = robust.fit_logistic(X, d["win"].to_numpy(float))
        model = {"weights": w.tolist(), "mu": mu.tolist(), "sd": sd.tolist(), "threshold": 0.5}
        idx = pd.bdate_range("2020-01-01", periods=400)
        c = pd.Series(np.linspace(100, 160, 400), index=idx)
        df = pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1000.0})
        p_up = robust.score_today(model, df, None, robust.PATTERN_NAMES[0], "bullish")
        p_dn = robust.score_today(model, df, None, robust.PATTERN_NAMES[0], "bearish")
        self.assertTrue(0 < p_up < 1 and 0 < p_dn < 1)
        self.assertGreater(p_up, p_dn)                           # rising stock: momentum favours the bullish signal


if __name__ == "__main__":
    unittest.main()
