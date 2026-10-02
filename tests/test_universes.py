import unittest
from datetime import date, timedelta

import numpy as np
import pandas as pd

from stock_agent import universes as U
from stock_agent.news import Headline, digest, recent


class UniverseTests(unittest.TestCase):
    def test_bundled_lists(self):
        self.assertEqual(len(U.get_universe("nifty50", offline=True)), 50)
        self.assertEqual(len(U.get_universe("nifty100", offline=True)), 100)
        fno = U.get_universe("fno", offline=True)
        self.assertGreater(len(fno), 150)
        self.assertNotIn("NIFTY.NS", fno)
        self.assertTrue(all(t.endswith(".NS") for t in fno))
        self.assertGreater(len(U.get_universe("nse_all", offline=True)), 1500)
        sp = U.get_universe("sp500", offline=True)
        self.assertGreater(len(sp), 450)
        self.assertTrue(all("." not in t for t in sp))   # Yahoo uses BRK-B, not BRK.B
        self.assertEqual(U.index_symbol("sp500"), "^GSPC")
        with self.assertRaises(KeyError):
            U.get_universe("nope")

    def test_recent_listings_sorted_and_bounded(self):
        r = U.recent_listings(3650, offline=True)
        self.assertTrue(r["listed"].is_monotonic_decreasing)
        self.assertGreater(len(r), 10)
        self.assertTrue(set(r.columns) >= {"symbol", "name", "series", "listed"})


class NewsDigestTests(unittest.TestCase):
    def _h(self, title, sent, days_ago):
        return Headline(title, "http://x", (date.today() - timedelta(days=days_ago)).isoformat(), "Src", sent, "")

    def test_digest_roles_and_recent(self):
        heads = [self._h("great results", 0.8, 1), self._h("probe launched", -0.6, 2), self._h("old news", 0.9, 40),
                 self._h("meh", 0.0, 3)]
        r = recent(heads, 14)
        self.assertEqual(len(r), 3)
        d = digest(r, bullish=True)
        roles = {i["title"]: i["role"] for i in d["items"]}
        self.assertEqual(roles["great results"], "supports")
        self.assertEqual(roles["probe launched"], "against")
        self.assertIn("1 support the trade, 1 go against it", d["summary"])
        self.assertEqual([i["date"] for i in d["items"]], sorted([i["date"] for i in d["items"]], reverse=True))
        put = digest(r, bullish=False)
        self.assertEqual({i["title"]: i["role"] for i in put["items"]}["probe launched"], "supports")
        self.assertIn("No recent headlines", digest([], True)["summary"])


class IpoTests(unittest.TestCase):
    def test_render_text(self):
        from stock_agent import ipo
        df = pd.DataFrame([{"symbol": "NEWCO", "ticker": "NEWCO.NS", "name": "New Co Ltd", "series": "EQ",
                            "listed": date(2026, 9, 1), "status": "trading", "listing_open": 100.0, "listing_close": 110.0,
                            "last": 120.0, "since_listing_pct": 0.2, "from_high_pct": -0.05, "day1_pct": 0.1},
                           {"symbol": "LATE", "ticker": "LATE.NS", "name": "Late Ltd", "series": "BE",
                            "listed": date(2026, 10, 1), "status": "no price data yet"}])
        txt = ipo.render_text(df, [], 30)
        self.assertIn("NEWCO", txt)
        self.assertIn("+20.0%", txt)
        self.assertIn("no price data yet", txt)
        self.assertIn("1 of 1 are above", txt)


if __name__ == "__main__":
    unittest.main()
