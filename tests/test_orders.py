"""Order record: planned price vs fill for broker and paper orders."""
import unittest

from stock_agent import orders


class OrderRecordTests(unittest.TestCase):
    def test_broker_entry_and_exits(self):
        journal = [{"contract": "TCS25OCT4000CE", "opened": "2026-10-01", "closed": "2026-10-03",
                    "kite": {"broker": "kite", "practice": False, "tradingsymbol": "TCS25OCT4000CE", "filled_qty": 350,
                             "avg_price": 41.0, "buy_price": 40.5, "planned": {"quote": 40.0, "limit": 40.5, "at": "2026-10-01T10:00:00"},
                             "gtt_legs": [{"stop": 30.0, "target": 55.0}],
                             "exits": [{"qty": 175, "price": 29.0, "via": "exit 1"}, {"qty": 175, "price": 55.5, "via": "exit 1"}]}}]
        r = orders.build(journal, None)
        buy = next(x for x in r["rows"] if x["action"] == "Buy")
        self.assertAlmostEqual(buy["slip"], 1.0)                       # paid 41 against a 40 ask
        self.assertAlmostEqual(buy["slip_rupees"], 350.0)
        stop = next(x for x in r["rows"] if x["action"] == "Sell at stop")
        self.assertAlmostEqual(stop["slip"], 1.0)                      # stop 30 filled at 29: one rupee worse
        tgt = next(x for x in r["rows"] if x["action"] == "Sell at target")
        self.assertAlmostEqual(tgt["slip"], -0.5)                      # better than planned: negative
        self.assertEqual(r["summary"]["Kite"]["orders"], 3)

    def test_paper_rows_and_short_sign(self):
        paper = {"positions": [{"symbol": "INFY.NS", "instrument": "stock", "side": "short", "qty": 10,
                                "quote": 1500.0, "entry_price": 1499.0, "entry_time": "2026-10-02T10:00"}],
                 "closed": [{"symbol": "TCS.NS", "instrument": "stock", "side": "long", "qty": 5, "quote": 4000.0,
                             "entry_price": 4002.0, "entry_time": "2026-10-01T10:00", "exit_planned": 3900.0,
                             "exit_quote": 3880.0, "exit_price": 3879.0, "exit_time": "2026-10-02T11:00",
                             "exit_reason": "Stop-loss hit"}]}
        r = orders.build([], paper)
        short = next(x for x in r["rows"] if x["symbol"] == "INFY")
        self.assertAlmostEqual(short["slip"], 1.0)                     # sold 1 below the quote
        stop = next(x for x in r["rows"] if "Stop" in x["action"])
        self.assertAlmostEqual(stop["slip"], 21.0)                     # gapped through the 3900 stop
        self.assertEqual(r["rows"][0]["time"], "2026-10-02T11:00")    # newest first


if __name__ == "__main__":
    unittest.main()
