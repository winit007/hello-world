"""Alerts: what fires, once a day, and where it goes."""
import shutil
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from stock_agent import alerts as AL


class AlertTests(unittest.TestCase):
    def test_position_events(self):
        items = [{"key": "a", "label": "TCS", "symbol": "TCS.NS", "stop": 3900, "target": 4400, "source": "My trades"},
                 {"key": "b", "label": "INFY put", "symbol": "INFY.NS", "bearish": True, "stop": 1600, "target": 1400},
                 {"key": "c", "label": "SBIN", "symbol": "SBIN.NS", "stop": 700, "exit_by": "2026-10-01"}]
        prices = {"TCS.NS": 3930.0, "INFY.NS": 1610.0, "SBIN.NS": 800.0}
        ev = {e["key"]: e for e in AL.position_events(items, prices.get, near_pct=1.0, today=date(2026, 10, 3))}
        self.assertIn("a:stop-near", ev)                      # 0.8% above the stop
        self.assertIn("b:stop-hit", ev)                       # a put's stop is above: 1610 >= 1600
        self.assertIn("c:exit-date", ev)
        self.assertNotIn("c:stop-near", ev)
        prices["TCS.NS"] = 4450.0
        self.assertIn("a:target", {e["key"] for e in AL.position_events(items[:1], prices.get)})

    def test_price_rules_fire_once(self):
        rules = [{"id": "r1", "symbol": "BTC-USD", "op": "above", "price": 100.0}, {"id": "r2", "symbol": "X.NS", "op": "below", "price": 5.0}]
        ev, fired = AL.price_rule_events(rules, {"BTC-USD": 101.0, "X.NS": 6.0}.get)
        self.assertEqual(fired, ["r1"])
        self.assertEqual(len(ev), 1)

    def test_store_sends_each_key_once_a_day(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        st = AL.Store(tmp / "alerts.json")
        d = st.load()
        e = [{"key": "k", "kind": "stop", "level": "high", "title": "t", "text": "x"}]
        self.assertEqual(len(st.add(d, e, datetime(2026, 10, 3, 10))), 1)
        self.assertEqual(len(st.add(d, e, datetime(2026, 10, 3, 14))), 0)
        self.assertEqual(len(st.add(d, e, datetime(2026, 10, 4, 10))), 1)
        st.save(d)
        self.assertEqual(len(st.load()["items"]), 2)

    def test_calendar_and_screen_change(self):
        self.assertEqual(AL.next_period_end(date(2026, 10, 3), "M"), date(2026, 10, 30))      # 31 Oct 2026 is a Saturday
        self.assertEqual(AL.next_period_end(date(2026, 10, 3), "Q"), date(2026, 12, 31))
        self.assertEqual(AL.next_period_end(date(2026, 2, 3), "H"), date(2026, 6, 30))
        self.assertEqual(AL.next_period_end(date(2026, 2, 3), "Y"), date(2026, 12, 31))
        self.assertIsNotNone(AL.rebalance_event(date(2026, 10, 30), today=date(2026, 10, 28)))
        self.assertIsNone(AL.rebalance_event(date(2026, 10, 30), today=date(2026, 10, 20)))
        ev = AL.screen_change_event(["A.NS", "B.NS"], ["B.NS", "C.NS"])
        self.assertEqual(ev["text"], "In: C. Out: A.")
        self.assertIsNone(AL.screen_change_event(["A.NS"], ["A.NS"]))

    def test_delivery_to_telegram(self):
        sent = []
        with mock.patch.object(AL, "telegram_send", side_effect=lambda t, c, m: sent.append((t, c, m))), \
                mock.patch.object(AL, "termux_available", return_value=False):
            problems = AL.deliver([{"title": "Stop-loss hit: TCS", "text": "at 3,890"}], {"telegram_token": "T", "telegram_chat": "42"})
        self.assertEqual(problems, [])
        self.assertEqual(sent[0][:2], ("T", "42"))
        self.assertIn("Stop-loss hit: TCS", sent[0][2])


class AppAlertTests(unittest.TestCase):
    def test_rules_config_and_check(self):
        from stock_agent import app, paper

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        patches = [mock.patch.object(app, n, tmp / f) for n, f in (("ALERTS_FILE", "alerts.json"), ("JOURNAL_FILE", "j.json"),
                                                                    ("HOLDINGS_FILE", "h.json"), ("PAPER_FILE", "p.json"))]
        for pt in patches:
            pt.start()
            self.addCleanup(pt.stop)
        app._write_json(tmp / "h.json", [{"id": "h1", "symbol": "TCS.NS", "qty": 1, "buy_price": 4000, "buy_date": "2025-01-01", "stop": 3900}])
        st = app.alerts_op("POST", "/api/alerts/rule", {"symbol": "infy", "op": "below", "price": 1500})
        self.assertEqual(st["rules"][0]["symbol"], "INFY.NS")
        st = app.alerts_op("POST", "/api/alerts/config", {"near_pct": 2, "telegram_token": "SECRET"})
        self.assertTrue(st["config"]["telegram"])
        self.assertNotIn("SECRET", str(st))                      # the token never goes back to the page
        prices = {"TCS.NS": 3880.0, "INFY.NS": 1450.0}
        with mock.patch.object(paper.PRICES, "last", side_effect=lambda s_: (prices[s_], "now")), \
                mock.patch.object(app, "paper_account", side_effect=paper.PaperError("none")), \
                mock.patch.object(AL, "deliver", return_value=[]):
            new = app.run_alert_check()
            again = app.run_alert_check()
        self.assertEqual({e["kind"] for e in new}, {"stop", "price"})
        self.assertEqual(again, [])                              # once a day
        st = app.alerts_state()
        self.assertEqual(st["rules"], [])                        # price alerts fire once
        self.assertEqual(st["unread"], 2)
        self.assertEqual(app.alerts_op("POST", "/api/alerts/read", {})["unread"], 0)


if __name__ == "__main__":
    unittest.main()
