"""Paper trading account: fills, charges, automatic exits. No network: prices and the clock are faked."""
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import pandas as pd

from stock_agent import paper
from stock_agent.paper import IST, Account, PaperError

T0 = datetime(2026, 9, 29, 10, 0, tzinfo=IST)   # a Tuesday, market open


class FakePrices:
    """5-minute bars per symbol; `extend` appends bars to simulate time passing."""

    def __init__(self, start=T0):
        self.data = {}
        self.start = start

    def set(self, symbol, closes, start=None, highs=None, lows=None):
        start = start or self.start - timedelta(minutes=5 * (len(closes) - 1))
        idx = pd.date_range(start, periods=len(closes), freq="5min")
        self.data[symbol] = pd.DataFrame({"Open": closes, "High": highs or closes, "Low": lows or closes,
                                          "Close": closes, "Volume": 1000}, index=idx)

    def extend(self, symbol, rows):
        df = self.data[symbol]
        idx = pd.date_range(df.index[-1] + timedelta(minutes=5), periods=len(rows), freq="5min")
        add = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx).assign(Volume=1000)
        self.data[symbol] = pd.concat([df, add])

    def bars(self, symbol):
        if symbol not in self.data:
            raise PaperError(f"No price found for {symbol}")
        return self.data[symbol]

    def last(self, symbol):
        df = self.bars(symbol)
        return float(df["Close"].iloc[-1]), df.index[-1].strftime("%d %b %H:%M")

    def usd_inr(self):
        return 90.0


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "paper.json"
        self.px = FakePrices()
        self.px.set("RELIANCE.NS", [1000.0, 1000.0, 1000.0])
        self.now = T0
        self.patches = [mock.patch.object(paper, "now_ist", lambda: self.now)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def acct(self):
        return Account(self.path, start_cash=200_000, prices=self.px)

    def test_delivery_buy_then_close_books_pnl_after_charges(self):
        a = self.acct()
        st = a.place({"instrument": "stock", "symbol": "RELIANCE", "side": "buy", "product": "delivery", "qty": 10})
        pos = st["positions"][0]
        self.assertEqual(pos["symbol"], "RELIANCE.NS")
        self.assertAlmostEqual(pos["entry_price"], 1000 * 1.0005)
        self.assertAlmostEqual(st["cash"], 200_000 - 10 * 1000.5 - pos["entry_charges"], places=2)
        self.px.extend("RELIANCE.NS", [(1100, 1100, 1100, 1100)])
        self.now = T0 + timedelta(minutes=10)
        st = self.acct().close(pos["id"])
        c = st["closed"][0]
        gross = (1100 * (1 - 0.0005) - 1000.5) * 10
        self.assertAlmostEqual(c["pnl"], gross - c["entry_charges"] - c["exit_charges"], places=2)
        self.assertGreater(c["entry_charges"], 0)
        self.assertAlmostEqual(st["equity"], 200_000 + c["pnl"], places=1)
        self.assertEqual(st["win_rate"], 1.0)
        self.assertEqual(st["equity_curve"][0]["equity"], 200_000)   # starting point survives snapshots
        self.assertEqual(st["positions"], [])

    def test_totals_predict_what_closing_everything_books(self):
        self.px.set("INFY.NS", [1500.0, 1500.0, 1500.0])
        a = self.acct()
        a.place({"instrument": "stock", "symbol": "RELIANCE", "side": "buy", "product": "delivery", "qty": 10})
        st = a.place({"instrument": "stock", "symbol": "INFY", "side": "buy", "product": "delivery", "qty": 4})
        self.px.extend("RELIANCE.NS", [(1100, 1100, 1100, 1100)])
        self.px.extend("INFY.NS", [(1440, 1440, 1440, 1440)])
        self.now = T0 + timedelta(minutes=10)
        st = self.acct().state()
        t = st["open_totals"]
        self.assertEqual(t["count"], 2)
        self.assertAlmostEqual(t["money_in"], sum(p["blocked"] for p in st["positions"]), places=2)
        self.assertAlmostEqual(t["money_in"], 10 * 1000.5 + 4 * 1500.75, places=1)
        self.assertEqual([round(p["blocked"], 1) for p in st["positions"]], [round(10 * 1000.5, 1), round(4 * 1500.75, 1)])   # each one separately
        self.assertGreater(t["exit_charges_est"], 0)
        self.assertAlmostEqual(t["return_pct"], t["net_pnl"] / t["money_in"], places=9)
        a = self.acct()
        for p in st["positions"]:
            st = a.close(p["id"])
        booked = sum(c["pnl"] for c in st["closed"])
        self.assertAlmostEqual(t["net_pnl"], booked, places=1)                       # the estimate was right
        ct = st["closed_totals"]
        self.assertEqual(ct["count"], 2)
        self.assertAlmostEqual(ct["net_pnl"], booked, places=2)
        self.assertAlmostEqual(ct["charges"], st["charges_paid"], places=2)
        self.assertAlmostEqual(ct["return_pct"], booked / ct["entry_value"], places=9)
        self.assertAlmostEqual(ct["money_in"], sum(c["blocked"] for c in st["closed"]), places=2)
        self.assertEqual(st["open_totals"]["count"], 0)
        self.assertIsNone(st["open_totals"]["return_pct"])

    def test_stop_checked_before_target_in_the_same_bar(self):
        a = self.acct()
        a.place({"instrument": "stock", "symbol": "RELIANCE.NS", "side": "buy", "product": "delivery", "qty": 5,
                 "stop": 980, "target": 1030})
        self.px.extend("RELIANCE.NS", [(1000, 1040, 970, 1000)])
        self.now = T0 + timedelta(minutes=5)
        st = self.acct().state()
        c = st["closed"][0]
        self.assertEqual(c["exit_reason"], "Stop-loss hit")
        self.assertAlmostEqual(c["exit_price"], 980 * (1 - 0.0005), places=3)
        self.assertTrue(st["events"])

    def test_target_hit_and_gap_through_stop_fills_at_open(self):
        a = self.acct()
        a.place({"instrument": "stock", "symbol": "RELIANCE.NS", "side": "buy", "product": "delivery", "qty": 5, "target": 1030})
        self.px.extend("RELIANCE.NS", [(1010, 1035, 1005, 1020)])
        self.now = T0 + timedelta(minutes=5)
        self.assertEqual(self.acct().state()["closed"][0]["exit_reason"], "Target hit")
        a = self.acct()
        a.place({"instrument": "stock", "symbol": "RELIANCE.NS", "side": "buy", "product": "delivery", "qty": 5, "stop": 1000})
        self.px.extend("RELIANCE.NS", [(950, 960, 940, 955)])   # gaps below the stop
        self.now += timedelta(minutes=5)
        c = self.acct().state()["closed"][0]
        self.assertEqual(c["exit_reason"], "Stop-loss hit")
        self.assertAlmostEqual(c["exit_price"], 950 * (1 - 0.0005), places=3)

    def test_intraday_short_squares_off_at_320(self):
        a = self.acct()
        st = a.place({"instrument": "stock", "symbol": "RELIANCE.NS", "side": "sell", "product": "intraday", "qty": 100})
        pos = st["positions"][0]
        self.assertEqual(pos["side"], "short")
        self.assertAlmostEqual(pos["blocked"], 100 * 1000 * (1 - 0.0005) * 0.20, places=2)
        # walk to 3:20 pm with the price falling
        n = (15 * 60 + 20 - (10 * 60)) // 5
        self.px.extend("RELIANCE.NS", [(990, 990, 990, 990)] * n)
        self.now = T0.replace(hour=15, minute=21)
        st = self.acct().state()
        c = st["closed"][0]
        self.assertEqual(c["exit_reason"], "Auto square-off")
        self.assertGreater(c["pnl"], 0)
        self.assertAlmostEqual(st["cash"], 200_000 + c["pnl"], places=1)

    def test_shorting_only_for_intraday_and_commodities(self):
        with self.assertRaises(PaperError):
            self.acct().preview({"instrument": "stock", "symbol": "RELIANCE.NS", "side": "sell", "product": "delivery", "qty": 1})
        with self.assertRaises(PaperError):
            self.acct().preview({"instrument": "crypto", "symbol": "BTC-USD", "side": "sell", "product": "positional", "qty": 1})

    def test_not_enough_cash_and_market_closed_block_the_order(self):
        pv = self.acct().preview({"instrument": "stock", "symbol": "RELIANCE.NS", "side": "buy", "product": "delivery", "qty": 500})
        self.assertFalse(pv["can_place"])
        self.assertIn("Not enough virtual cash", pv["warnings"][0])
        with self.assertRaises(PaperError):
            self.acct().place({"instrument": "stock", "symbol": "RELIANCE.NS", "side": "buy", "product": "delivery", "qty": 500})
        self.now = T0.replace(hour=18)
        pv = self.acct().preview({"instrument": "stock", "symbol": "RELIANCE.NS", "side": "buy", "product": "delivery", "qty": 1})
        self.assertIn("Market is closed", pv["warnings"][0])

    def test_stop_on_the_wrong_side_is_rejected(self):
        with self.assertRaises(PaperError):
            self.acct().preview({"instrument": "stock", "symbol": "RELIANCE.NS", "side": "buy", "product": "delivery", "qty": 1, "stop": 1010})

    def test_crypto_converts_dollars_and_trades_at_night(self):
        self.px.set("BTC-USD", [60_000.0, 60_000.0])
        self.now = T0.replace(hour=23)
        st = self.acct().place({"instrument": "crypto", "symbol": "BTC-USD", "side": "buy", "product": "positional", "qty": 0.01})
        p = st["positions"][0]
        self.assertAlmostEqual(p["entry_price"], 60_000 * 90 * 1.001, places=2)
        self.assertAlmostEqual(st["cash"], 200_000 - 0.01 * p["entry_price"] - p["entry_charges"], places=1)

    def test_commodity_lots_margin_and_short(self):
        self.px.set("CL=F", [80.0, 80.0])
        st = self.acct().place({"instrument": "commodity", "symbol": "CL=F", "side": "sell", "product": "intraday", "lots": 2, "stop": 81})
        p = st["positions"][0]
        self.assertEqual(p["qty"], 20)                       # CRUDEOILM: 10 barrels per lot
        self.assertAlmostEqual(p["blocked"], 20 * 80 * 90 * (1 - 0.0002) * 0.10, places=2)
        self.px.extend("CL=F", [(80.5, 81.5, 80.4, 81.2)])
        self.now = T0 + timedelta(minutes=5)
        c = self.acct().state()["closed"][0]
        self.assertEqual(c["exit_reason"], "Stop-loss hit")
        self.assertLess(c["pnl"], 0)
        self.assertLess(self.acct().state(refresh=False)["max_drawdown"], 0)

    def test_option_buy_uses_lot_size_and_expires_at_intrinsic(self):
        with mock.patch("stock_agent.lots.lot_size", return_value=(500, "test")), \
                mock.patch("stock_agent.data.load_prices", side_effect=OSError("offline")):
            st = self.acct().place({"instrument": "option", "symbol": "RELIANCE", "side": "buy", "product": "positional",
                                    "lots": 1, "option_type": "CE", "strike": 1000, "expiry": "2026-10-27"})
        p = st["positions"][0]
        self.assertEqual((p["qty"], p["lot_size"], p["iv"]), (500, 500, 0.30))
        self.assertGreater(p["entry_price"], 10)
        # expiry passes with the stock at 1050: settled at intrinsic 50 less slippage
        self.px.extend("RELIANCE.NS", [(1050, 1050, 1050, 1050)])
        self.now = datetime(2026, 10, 28, 10, 0, tzinfo=IST)
        c = self.acct().state()["closed"][0]
        self.assertEqual(c["exit_reason"], "Expired")
        self.assertAlmostEqual(c["exit_price"], 50 * 0.99, places=3)

    def opt(self, **kw):
        order = {"instrument": "option", "symbol": "RELIANCE", "product": "positional", "lots": 1,
                 "option_type": "CE", "strike": 1000, "expiry": "2026-10-27", **kw}
        with mock.patch("stock_agent.lots.lot_size", return_value=(500, "test")), \
                mock.patch("stock_agent.data.load_prices", side_effect=OSError("offline")):
            return self.acct().place(order)

    def test_sold_call_blocks_margin_and_is_stopped_by_a_rally(self):
        with self.assertRaises(PaperError):          # a sold call loses when the share rises: stop goes above
            self.opt(side="sell", stop=990)
        st = self.opt(side="sell", stop=1020)
        p = st["positions"][0]
        self.assertEqual(p["side"], "short")
        self.assertAlmostEqual(p["blocked"], 0.18 * 1000 * 500, places=2)
        self.assertAlmostEqual(st["cash"], 200_000 - p["blocked"] - p["entry_charges"], places=2)
        self.px.extend("RELIANCE.NS", [(1005, 1025, 1004, 1022)])
        self.now = T0 + timedelta(minutes=5)
        st = self.acct().state()
        c = st["closed"][0]
        self.assertEqual(c["exit_reason"], "Stop-loss hit")
        self.assertGreater(c["exit_price"], c["entry_price"])      # bought back dearer
        self.assertLess(c["pnl"], 0)
        self.assertAlmostEqual(st["cash"], 200_000 + c["pnl"], places=1)

    def test_sold_put_expiring_worthless_keeps_the_premium(self):
        st = self.opt(side="sell", option_type="PE", strike=950)
        p = st["positions"][0]
        premium = p["entry_price"] * p["qty"]
        self.px.extend("RELIANCE.NS", [(1040, 1040, 1040, 1040)])
        self.now = datetime(2026, 10, 28, 10, 0, tzinfo=IST)
        c = self.acct().state()["closed"][0]
        self.assertEqual((c["exit_reason"], c["exit_price"]), ("Expired", 0.0))
        self.assertAlmostEqual(c["pnl"], premium - c["entry_charges"] - c["exit_charges"], places=2)
        self.assertGreater(c["pnl"], 0)

    def test_bought_put_stop_sits_above_the_share_price(self):
        with self.assertRaises(PaperError):
            self.opt(side="buy", option_type="PE", stop=980)
        st = self.opt(side="buy", option_type="PE", stop=1015, target=970)
        self.assertAlmostEqual(st["positions"][0]["blocked"], st["positions"][0]["entry_price"] * 500, places=1)   # paid in full
        self.px.extend("RELIANCE.NS", [(1000, 1016, 999, 1012)])
        self.now = T0 + timedelta(minutes=5)
        c = self.acct().state()["closed"][0]
        self.assertEqual(c["exit_reason"], "Stop-loss hit")
        self.assertLess(c["pnl"], 0)

    def test_preview_of_a_sold_option_explains_the_risk(self):
        with mock.patch("stock_agent.lots.lot_size", return_value=(500, "test")), \
                mock.patch("stock_agent.data.load_prices", side_effect=OSError("offline")):
            pv = self.acct().preview({"instrument": "option", "symbol": "RELIANCE", "side": "sell", "product": "intraday",
                                      "lots": 1, "option_type": "CE", "strike": 1000, "expiry": "2026-10-27", "stop": 1020})
        self.assertTrue(pv["can_place"])
        self.assertIn("no limit", pv["notes"][0])
        self.assertGreater(pv["risk_at_stop"], 0)

    def test_fit_suggests_fewer_shares_that_the_cash_covers(self):
        self.acct().reset(25_000)
        pv = self.acct().preview({"instrument": "stock", "symbol": "RELIANCE.NS", "side": "buy", "product": "delivery", "qty": 30})
        self.assertFalse(pv["can_place"])
        q = pv["fit"]["order"]["qty"]
        self.assertEqual(q, 24)                                   # 25 x 1000.5 + charges > 25,000
        ok = self.acct().preview({"instrument": "stock", "symbol": "RELIANCE.NS", "side": "buy", "product": "delivery", "qty": q})
        self.assertTrue(ok["can_place"])
        self.assertIsNone(ok["fit"])

    def test_fit_suggests_a_cheaper_strike_for_a_dear_option(self):
        self.acct().reset(25_000)
        order = {"instrument": "option", "symbol": "RELIANCE", "side": "buy", "product": "positional", "lots": 1,
                 "option_type": "CE", "strike": 900, "expiry": "2026-10-27"}            # deep in the money: ~₹50,000
        with mock.patch("stock_agent.lots.lot_size", return_value=(500, "test")), \
                mock.patch("stock_agent.data.load_prices", side_effect=OSError("offline")):
            pv = self.acct().preview(order)
            fit = pv["fit"]["order"]
            self.assertGreater(fit["strike"], 900)                # further out of the money for a call
            self.assertIn("wins less often", pv["fit"]["text"])
            ok = self.acct().preview({**order, **fit})
            put = self.acct().preview({**order, "option_type": "PE", "strike": 1100})
        self.assertTrue(ok["can_place"])
        self.assertLess(put["fit"]["order"]["strike"], 1100)      # cheaper puts are lower strikes

    def test_fit_for_lots_and_when_nothing_fits(self):
        self.px.set("CL=F", [80.0])
        self.acct().reset(50_000)
        pv = self.acct().preview({"instrument": "commodity", "symbol": "CL=F", "side": "buy", "product": "intraday", "lots": 10})
        self.assertEqual(pv["fit"]["order"], {"lots": 6})        # a lot blocks ₹7,200 + charges: 7 lots > ₹50,000
        self.acct().reset(5_000)
        pv = self.acct().preview({"instrument": "commodity", "symbol": "CL=F", "side": "buy", "product": "intraday", "lots": 1})
        self.assertIsNone(pv["fit"]["order"])
        self.assertIn("Even 1 lot", pv["fit"]["text"])

    def test_reset_and_persistence(self):
        a = self.acct()
        a.place({"instrument": "stock", "symbol": "RELIANCE.NS", "side": "buy", "product": "delivery", "qty": 1})
        self.assertEqual(len(self.acct().data["positions"]), 1)
        st = self.acct().reset(50_000)
        self.assertEqual((st["cash"], st["equity"], st["positions"]), (50_000, 50_000, []))
        with self.assertRaises(PaperError):
            self.acct().reset(10)

    def test_add_money_keeps_positions_and_profit(self):
        a = self.acct()
        a.place({"instrument": "stock", "symbol": "RELIANCE.NS", "side": "buy", "product": "delivery", "qty": 10})
        before = self.acct().state(refresh=False)
        st = self.acct().add_money(100_000)
        self.assertEqual(len(st["positions"]), 1)                       # still open
        self.assertAlmostEqual(st["cash"], before["cash"] + 100_000, places=2)
        self.assertAlmostEqual(st["start_cash"], 300_000)
        self.assertAlmostEqual(st["pnl"], before["pnl"], places=2)     # profit is what the trades earned, not the deposit
        self.assertAlmostEqual(st["equity"], before["equity"] + 100_000, places=2)
        self.assertEqual(len(self.acct().data["positions"]), 1)         # saved
        self.assertEqual(self.acct().data["deposits"][0]["amount"], 100_000)
        out = self.acct().add_money(-50_000)                            # free cash can be taken out again
        self.assertAlmostEqual(out["start_cash"], 250_000)
        with self.assertRaises(PaperError):
            self.acct().add_money(-1_000_000)                           # more than the free cash
        with self.assertRaises(PaperError):
            self.acct().add_money(0)

    def test_charges_are_plausible(self):
        self.assertAlmostEqual(paper.charges("stock", "delivery", "buy", 100_000), 100 + 2.97 + 15 + 0.1 + 0.18 * 3.07, places=1)
        # ₹10 lakh intraday buy: ₹20 brokerage + exchange ₹29.7 + stamp ₹30 + SEBI ₹1 + GST on them ≈ ₹90
        self.assertAlmostEqual(paper.charges("stock", "intraday", "buy", 1_000_000), 89.83, places=1)
        self.assertEqual(paper.charges("stock", "delivery", "buy", 0), 0.0)


class PaperApiTests(unittest.TestCase):
    def test_endpoints_return_400_on_bad_orders(self):
        import json
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer

        from stock_agent import app

        tmp = tempfile.TemporaryDirectory()
        px = FakePrices()
        px.set("RELIANCE.NS", [1000.0])
        with mock.patch.object(app, "PAPER_FILE", Path(tmp.name) / "paper.json"), \
                mock.patch.object(app, "SETTINGS_FILE", Path(tmp.name) / "settings.json"), \
                mock.patch.object(paper, "PRICES", px), mock.patch.object(paper, "now_ist", lambda: T0):
            srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{srv.server_address[1]}"

            def call(path, body=None):
                req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                             headers={"X-Agent-Token": app.SESSION_TOKEN, "Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req) as r:
                        return r.status, json.loads(r.read())
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read())
            try:
                code, st = call("/api/paper?refresh=0")
                self.assertEqual((code, st["cash"]), (200, 200_000))
                code, err = call("/api/paper/order", {"instrument": "stock", "symbol": "RELIANCE.NS", "side": "sell",
                                                      "product": "delivery", "qty": 1})
                self.assertEqual(code, 400)
                self.assertIn("short", err["error"])
                code, st = call("/api/paper/order", {"instrument": "stock", "symbol": "RELIANCE.NS", "side": "buy",
                                                     "product": "delivery", "qty": 2})
                self.assertEqual((code, len(st["positions"])), (200, 1))
                code, st = call("/api/paper/close/" + st["positions"][0]["id"], {})
                self.assertEqual((code, len(st["closed"])), (200, 1))
                code, st = call("/api/paper/reset", {"start_cash": 100_000})
                self.assertEqual((code, st["cash"]), (200, 100_000))
            finally:
                srv.shutdown()
                tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
