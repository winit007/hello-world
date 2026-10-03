"""Portfolio risk: exposure, shocks, VaR, stops and warnings on synthetic prices."""
import unittest
from datetime import date, timedelta

import numpy as np
import pandas as pd

from stock_agent import risk as R


def series(seed, beta=1.0, base=None, n=600):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n)
    mkt = base if base is not None else rng.normal(0.0004, 0.01, n)
    r = beta * mkt + rng.normal(0, 0.005, n)
    return pd.Series(100 * np.cumprod(1 + r), index=idx), mkt


class RiskTests(unittest.TestCase):
    def setUp(self):
        _, mkt = series(1)
        self.bench = pd.Series(100 * np.cumprod(1 + mkt), index=pd.bdate_range("2023-01-02", periods=len(mkt)))
        self.a, _ = series(2, beta=1.5, base=mkt)
        self.b, _ = series(3, beta=0.5, base=mkt)
        self.hist = {"A.NS": self.a, "B.NS": self.b}

    def test_stock_exposure_beta_and_shock(self):
        pos = R.from_holdings([{"symbol": "A.NS", "qty": 100}, {"symbol": "B.NS", "qty": 100}], fx=85)
        r = R.analyze(pos, self.hist, self.bench, 1e6, {"A.NS": "IT", "B.NS": "FMCG"})
        a = next(x for x in r["positions"] if x["symbol"] == "A.NS")
        self.assertAlmostEqual(a["exposure"], a["value"], places=6)          # a share's delta is its value
        self.assertAlmostEqual(a["beta"], 1.5, delta=0.15)
        self.assertAlmostEqual(a["down5"], -a["value"] * a["beta"] * 0.05, places=6)
        self.assertLess(r["down5"], 0)
        self.assertGreater(r["var95"], 0)
        self.assertGreaterEqual(r["es95"], r["var95"])
        self.assertEqual(r["risk_at_stops"], 0)
        self.assertFalse(any("No stop-loss" in w for w in r["warnings"]))      # holdings need no stop
        self.assertAlmostEqual(sum(s["share"] for s in r["sectors"]), 1.0)

    def test_bought_put_hedges_and_stop_loss(self):
        exp = (date.today() + timedelta(days=30)).isoformat()
        spot = float(self.a.iloc[-1])
        journal = [{"status": "open", "ticker": "A.NS", "side": "PUT", "lots": 2, "lot_size": 50, "entry_premium": 3.0,
                    "stop": spot * 1.03, "kite": {"contract": {"strike": round(spot), "expiry": exp}}}]
        pos = R.from_journal(journal, lambda s: 0.3)
        r = R.analyze(pos, self.hist, self.bench, 1e6, {})
        p = r["positions"][0]
        self.assertLess(p["exposure"], 0)                     # a put gains as the share falls
        self.assertGreater(p["down5"], 0)
        self.assertLess(p["loss_at_stop"], 0)                 # stop above the price: a loss when hit
        self.assertLessEqual(-p["loss_at_stop"], p["value"])  # never more than the premium paid

    def test_warnings_for_concentration_and_missing_stops(self):
        class Acct:
            data = {"positions": [{"id": "x", "symbol": "A.NS", "instrument": "stock", "side": "long", "qty": 5000,
                                   "stop": None}]}

            def _unit_price(self, p, spot, at=None):
                return spot

            def _label(self, p):
                return "A × 5000"
        r = R.analyze(R.from_paper(Acct()), self.hist, self.bench, 2e5, {})
        text = " ".join(r["warnings"])
        self.assertIn("No stop-loss on: A × 5000", text)
        self.assertIn("of your capital in one position", text)

    def test_empty(self):
        self.assertTrue(R.analyze([], {}, self.bench, 1e5, {})["empty"])


if __name__ == "__main__":
    unittest.main()
