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


class TrackerTests(unittest.TestCase):
    def _rec(self, **kw):
        base = {"as_of": "2026-01-05", "rank": 1, "ticker": "TEST.NS", "side": "CALL", "pattern": "Hammer",
                "success": 0.6, "baseline": 0.55, "close": 100.0, "strike": 100.0, "expiry": "2026-01-27",
                "premium": 3.0, "lot_size": 100, "lots": 2, "stop": 98.0, "target1": 103.0, "target2": 105.0,
                "time_exit": "2026-01-12"}
        return {**base, **kw}

    def _df(self, bars):
        idx = pd.bdate_range("2026-01-05", periods=len(bars))
        return pd.DataFrame(bars, columns=["Open", "High", "Low", "Close"], index=idx).assign(Volume=1.0)

    def test_targets_split_and_daily_sum(self):
        from stock_agent import tracker
        df = self._df([(100, 100, 100, 100), (100.5, 103.5, 100.2, 103), (103, 105.5, 102.8, 105)])
        x = tracker.simulate(self._rec(), df)
        self.assertEqual([e["reason"] for e in x["exits"]], ["Target 1", "Target 2"])
        self.assertEqual(x["status"], "Target 2")
        self.assertGreater(x["pnl"], 0)
        self.assertAlmostEqual(sum(x["daily"].values()), x["pnl"], places=1)

    def test_intraday_stop_and_gap(self):
        from stock_agent import tracker
        # day 2 trades through the stop intraday but closes above it: still stopped out
        df = self._df([(100, 100, 100, 100), (100, 100.5, 97.5, 99.5), (99.5, 104, 99, 104)])
        x = tracker.simulate(self._rec(lots=1), df)
        self.assertEqual(x["status"], "Stop")
        self.assertEqual(len(x["exits"]), 1)
        # a gap below the stop after entry fills at the open, which is worse than the stop level
        gap = tracker.simulate(self._rec(lots=1), self._df([(100, 100, 100, 100), (100, 100.5, 99, 99.5), (95, 96, 94, 95.5)]))
        stop = tracker.simulate(self._rec(lots=1), self._df([(100, 100, 100, 100), (100, 100.5, 99, 99.5), (99, 99.5, 97.9, 99)]))
        self.assertEqual(gap["status"], "Stop")
        self.assertLess(gap["pnl"], stop["pnl"])
        # opening below the stop on the entry morning: the trade is skipped, not counted
        skip = tracker.simulate(self._rec(lots=1), self._df([(100, 100, 100, 100), (95, 96, 94, 95.5)]))
        self.assertEqual((skip["status"], skip["pnl"]), ("Skipped", 0.0))

    def test_time_exit_and_waiting(self):
        from stock_agent import tracker
        flat = [(100, 100.5, 99.5, 100)] * 8
        x = tracker.simulate(self._rec(lots=1), self._df(flat))
        self.assertEqual(x["status"], "Time exit")
        self.assertEqual(x["exits"][0]["date"], "2026-01-12")
        w = tracker.simulate(self._rec(), self._df([(100, 100, 100, 100)]))
        self.assertEqual(w["status"], "waiting")

    def test_put_and_unaffordable(self):
        from stock_agent import tracker
        df = self._df([(100, 100, 100, 100), (99.5, 99.8, 96.8, 97)])
        x = tracker.simulate(self._rec(side="PUT", lots=0, stop=102.0, target1=97.0, target2=95.0), df)
        self.assertFalse(x["affordable"])
        self.assertEqual(x["sim_lots"], 1)
        self.assertEqual(x["status"], "Target 1")
        ev = tracker.evaluate([self._rec(side="PUT", lots=0, stop=102.0, target1=97.0, target2=95.0)], {"TEST.NS": df})
        self.assertEqual(ev["account"]["trades"], 0)
        self.assertEqual(ev["all"]["wins"], 1)

    def test_ledger_record_replaces_same_day(self):
        import tempfile
        from pathlib import Path
        from stock_agent import tracker
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.json"
            tracker.save_ledger(path, [self._rec(), self._rec(as_of="2026-01-06")])
            tracker.record(path, [], {"as_of": "2026-01-06"})
            self.assertEqual([r["as_of"] for r in tracker.load_ledger(path)], ["2026-01-05"])
