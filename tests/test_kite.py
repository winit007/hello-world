import hashlib
import json
import tempfile
import threading
import unittest
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from stock_agent import kite as K

EXPIRY = (date.today() + timedelta(days=25)).isoformat()


class FakeKite(BaseHTTPRequestHandler):
    state: dict = {}

    def log_message(self, *a):
        pass

    def _json(self, data, code=200):
        body = json.dumps({"status": "success" if code < 400 else "error", "data": data,
                           **({"message": data} if code >= 400 else {})}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        return self.headers.get("Authorization") == "token key1:tok1"

    def do_GET(self):
        st, u = self.state, urlparse(self.path)
        if not self._auth():
            return self._json("bad token", 403)
        if u.path == "/instruments/NFO":
            rows = ["instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange"]
            for k in (920, 940, 960):
                for t in ("CE", "PE"):
                    rows.append(f"1,1,HINDALCO26OCT{k}{t},HINDALCO,0,{EXPIRY},{k}.0,0.05,700,{t},NFO-OPT,NFO")
            body = "\n".join(rows).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(body); return
        if u.path == "/quote":
            if st.get("no_data"):
                return self._json("Insufficient permission for that call.", 403)
            keys = parse_qs(u.query)["i"]
            out = {}
            for k in keys:
                if k.startswith("NFO:"):
                    out[k] = {"last_price": st["ltp"], "depth": {"buy": [{"price": st["ltp"] - 0.1}], "sell": [{"price": st["ltp"] + 0.1}]}}
                else:
                    out[k] = {"last_price": st.get("spot", 941.85)}
            return self._json(out)
        if u.path == "/user/margins/equity":
            return self._json({"available": {"live_balance": st.get("funds", 100000)}, "net": 100000})
        if u.path.startswith("/orders/"):
            oid = u.path.rsplit("/", 1)[-1]
            o = st["orders"][oid]
            return self._json([{"status": o["status"], "filled_quantity": o["filled"], "average_price": o["avg"]}])
        if u.path.startswith("/gtt/triggers/"):
            g = st["gtts"][u.path.rsplit("/", 1)[-1]]
            legs = [{"result": {"order_result": {"order_id": g["exit_order"]}}} if g.get("exit_order") else {}]
            return self._json({"status": g["status"], "orders": legs})
        return self._json("nope", 404)

    def do_POST(self):
        st, u = self.state, urlparse(self.path)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
        if u.path == "/session/token":
            ok = form["checksum"] == hashlib.sha256(b"key1" + form["request_token"].encode() + b"sec1").hexdigest()
            return self._json({"access_token": "tok1", "user_name": "Test User"} if ok else "bad checksum", 200 if ok else 403)
        if not self._auth():
            return self._json("bad token", 403)
        if u.path == "/orders/regular":
            oid = str(1000 + len(st["orders"]))
            st["orders"][oid] = {"form": form, "status": st.get("fill_status", "COMPLETE"),
                                 "filled": int(form["quantity"]) if st.get("fill_status", "COMPLETE") == "COMPLETE" else 0,
                                 "avg": float(form["price"])}
            return self._json({"order_id": oid})
        if u.path == "/gtt/triggers":
            gid = str(500 + len(st["gtts"]))
            st["gtts"][gid] = {"condition": json.loads(form["condition"]), "orders": json.loads(form["orders"]),
                               "type": form["type"], "status": "active"}
            return self._json({"trigger_id": gid})
        return self._json("nope", 404)

    def do_DELETE(self):
        gid = urlparse(self.path).path.rsplit("/", 1)[-1]
        self.state["gtts"][gid]["status"] = "cancelled"
        return self._json({"trigger_id": gid})


PLAN = {"side": "CALL", "expiry": EXPIRY, "strike": 940.0, "entry": 941.85, "premium": 29.9,
        "stop_premium": 21.95, "target1_premium": 42.65, "target2_premium": 53.0,
        "stop_underlying": 927.34, "target1_underlying": 963.61, "target2_underlying": 978.12, "time_stop": EXPIRY}


class KiteFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeKite)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.root_patch = mock.patch.object(K, "API_ROOT", f"http://127.0.0.1:{cls.srv.server_address[1]}")
        cls.root_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.root_patch.stop()
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        FakeKite.state = {"orders": {}, "gtts": {}, "ltp": 29.9}
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.k = K.Kite(self.home)
        self.k.update_config("key1", "sec1", practice=False)
        self.k.complete_login("req1")
        self.open_patch = mock.patch.object(K, "market_open", return_value=True)
        self.open_patch.start()
        # checklist inputs: a positive strategy check and no results date, so no test touches Yahoo
        from stock_agent import checklist, tradetest
        self.val_patch = mock.patch.object(tradetest, "load_validation", return_value={
            "test_avg_ret": 0.01, "test_win_rate": 0.55, "test_trades": 1000})
        self.ev_patch = mock.patch.object(checklist, "next_results_date", return_value=None)
        self.val_patch.start(); self.ev_patch.start()
        real_place = K.place
        self.place_patch = mock.patch.object(K, "place", side_effect=lambda kite, pv, wait_seconds=20, confirmed=None:
                                             real_place(kite, pv, wait_seconds, ["liquid", "events"] if confirmed is None else confirmed))
        self.place_patch.start()

    def tearDown(self):
        self.open_patch.stop()
        self.val_patch.stop(); self.ev_patch.stop(); self.place_patch.stop()
        self.tmp.cleanup()

    def pv(self, capital=200000, risk=0.02, **kw):
        return K.preview(self.k, self.home, "HINDALCO.NS", {**PLAN, **kw}, capital, risk)

    def test_login_and_status(self):
        st = self.k.status()
        self.assertTrue(st["connected"])
        self.assertEqual(st["user"], "Test User")
        self.assertIn("api_key=key1", self.k.login_url())

    def test_preview_contract_and_sizing(self):
        pv = self.pv(capital=1_000_000)
        self.assertEqual(pv["tradingsymbol"], "HINDALCO26OCT940CE")
        self.assertEqual(pv["lot_size"], 700)
        self.assertAlmostEqual(pv["limit"], 30.0)                 # best ask
        self.assertLess(pv["levels"]["stop"], pv["limit"])
        self.assertLess(pv["limit"], pv["levels"]["t1"])
        self.assertLessEqual(pv["max_loss_at_stop"], 1_000_000 * 0.02 + 1e-6)
        self.assertEqual(pv["qty"], pv["lots"] * 700)
        self.assertFalse(pv["blockers"])

    def test_preview_blocks_insufficient_funds(self):
        FakeKite.state["funds"] = 5_000
        self.assertTrue(any("Not enough funds" in b for b in self.pv(capital=1_000_000)["blockers"]))

    def test_preview_blocks_unaffordable_and_closed_market(self):
        self.assertTrue(any("above your" in b for b in self.pv(capital=50_000)["blockers"]))
        with mock.patch.object(K, "market_open", return_value=False):
            self.assertTrue(any("Market is closed" in b for b in self.pv(capital=1_000_000)["blockers"]))
        with self.assertRaises(K.KiteError):
            K.place(self.k, self.pv(capital=50_000))

    def test_practice_sends_nothing(self):
        self.k.update_config(practice=True)
        rec = K.place(self.k, self.pv(capital=1_000_000))
        self.assertTrue(rec["practice"])
        self.assertEqual(FakeKite.state["orders"], {})
        self.assertEqual(FakeKite.state["gtts"], {})

    def test_live_buy_then_split_gtts(self):
        FakeKite.state["funds"] = 500_000
        pv = self.pv(capital=1_000_000, risk=0.05)
        self.assertGreaterEqual(pv["lots"], 2)
        rec = K.place(self.k, pv, wait_seconds=2)
        buy = next(iter(FakeKite.state["orders"].values()))["form"]
        self.assertEqual((buy["transaction_type"], buy["order_type"], buy["product"], buy["exchange"]), ("BUY", "LIMIT", "NRML", "NFO"))
        self.assertEqual(int(buy["quantity"]), pv["qty"])
        gtts = list(FakeKite.state["gtts"].values())
        self.assertEqual(len(gtts), 2)
        self.assertEqual(sum(g["orders"][0]["quantity"] for g in gtts), pv["qty"])
        for g in gtts:
            lo, hi = g["condition"]["trigger_values"]
            self.assertLess(lo, g["condition"]["last_price"])
            self.assertLess(g["condition"]["last_price"], hi)
            self.assertEqual(g["type"], "two-leg")
            self.assertTrue(all(o["transaction_type"] == "SELL" for o in g["orders"]))
            self.assertLessEqual(g["orders"][0]["price"], lo)   # stop leg sells at/below its trigger
        self.assertEqual(len(rec["gtt_ids"]), 2)

    def test_no_gtt_until_filled(self):
        FakeKite.state["fill_status"] = "OPEN"
        rec = K.place(self.k, self.pv(capital=1_000_000), wait_seconds=0)
        self.assertEqual(FakeKite.state["gtts"], {})
        self.assertEqual(rec["gtt_ids"], [])
        o = FakeKite.state["orders"][rec["buy_order_id"]]
        o.update(status="COMPLETE", filled=rec["quantity"])
        K.sync_one(self.k, rec)
        self.assertEqual(len(FakeKite.state["gtts"]), len(rec["gtt_legs"]))

    def test_rejected_buy_places_nothing_more(self):
        FakeKite.state["fill_status"] = "REJECTED"
        rec = K.place(self.k, self.pv(capital=1_000_000), wait_seconds=1)
        self.assertEqual(rec["buy_status"], "REJECTED")
        self.assertEqual(FakeKite.state["gtts"], {})

    def test_gtt_exit_closes_journal_entry(self):
        from stock_agent import app

        rec = K.place(self.k, self.pv(capital=1_000_000), wait_seconds=1)
        gid = rec["gtt_ids"][0]
        FakeKite.state["orders"]["9999"] = {"status": "COMPLETE", "filled": rec["filled_qty"], "avg": 42.0}
        FakeKite.state["gtts"][gid].update(status="triggered", exit_order="9999")
        K.sync_one(self.k, rec)
        e = {"status": "open", "kite": rec}
        app._apply_kite(e)
        self.assertEqual(e["status"], "closed")
        self.assertAlmostEqual(e["pnl"], (42.0 - rec["avg_price"]) * rec["filled_qty"])

    def test_no_market_data_plan(self):
        FakeKite.state["no_data"] = True
        pv = self.pv(capital=1_000_000)
        self.assertIsNone(pv["live"])
        self.assertTrue(any("no live quotes" in w for w in pv["warnings"]))
        self.assertAlmostEqual(pv["limit"], PLAN["premium"], places=1)

    def test_exit_now_cancels_gtts_and_sells(self):
        rec = K.place(self.k, self.pv(capital=1_000_000), wait_seconds=1)
        K.exit_now(self.k, rec)
        self.assertTrue(all(g["status"] == "cancelled" for g in FakeKite.state["gtts"].values()))
        sells = [o["form"] for o in FakeKite.state["orders"].values() if o["form"]["transaction_type"] == "SELL"]
        self.assertEqual(int(sells[0]["quantity"]), rec["filled_qty"])


class ChecklistGateTests(KiteFlowTests):
    """Live orders need every checklist answer to be yes; practice orders do not."""

    def test_live_order_blocked_when_strategy_has_no_edge(self):
        from stock_agent import tradetest
        with mock.patch.object(tradetest, "load_validation", return_value={"test_avg_ret": -0.02, "test_win_rate": 0.38, "test_trades": 13000}):
            pv = self.pv(capital=1_000_000)
            self.assertEqual(next(i for i in pv["checklist"]["items"] if i["id"] == "edge")["status"], "fail")
            with self.assertRaises(K.KiteError) as cm:
                K.place(self.k, pv, confirmed=["liquid", "events"])
            self.assertIn("Proven edge", str(cm.exception))
            self.assertEqual(FakeKite.state["orders"], {})
            self.k.update_config(practice=True)
            rec = K.place(self.k, self.pv(capital=1_000_000))
            self.assertTrue(rec["practice"])

    def test_live_order_needs_ticks_for_manual_items(self):
        pv = self.pv(capital=1_000_000)
        with self.assertRaises(K.KiteError) as cm:
            K.place(self.k, pv, confirmed=[])
        self.assertIn("No big events", str(cm.exception))
        self.assertEqual(FakeKite.state["orders"], {})

    # run only the two gate tests here; the inherited flow tests already run in KiteFlowTests
    for _name in [n for n in dir(KiteFlowTests) if n.startswith("test_")]:
        locals()[_name] = None
    del _name


class AppSecurityTests(unittest.TestCase):
    def test_token_and_host_required(self):
        import os
        import urllib.error
        import urllib.request

        with tempfile.TemporaryDirectory() as home:
            os.environ["STOCK_AGENT_HOME"] = home
            import importlib

            from stock_agent import app
            app = importlib.reload(app)
            srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            try:
                page = urllib.request.urlopen(base + "/").read().decode()
                self.assertIn(app.SESSION_TOKEN, page)
                for headers in ({}, {"X-Agent-Token": "wrong"}):
                    req = urllib.request.Request(base + "/api/kite/config", data=b'{"practice": false}', headers=headers)
                    with self.assertRaises(urllib.error.HTTPError) as cm:
                        urllib.request.urlopen(req)
                    self.assertEqual(cm.exception.code, 403)
                req = urllib.request.Request(base + "/api/settings", headers={"Host": "evil.example", "X-Agent-Token": app.SESSION_TOKEN})
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(req)
                self.assertEqual(cm.exception.code, 403)
                req = urllib.request.Request(base + "/api/kite/status", headers={"X-Agent-Token": app.SESSION_TOKEN})
                self.assertFalse(json.loads(urllib.request.urlopen(req).read())["connected"])
            finally:
                srv.shutdown(); srv.server_close()
                os.environ.pop("STOCK_AGENT_HOME", None)


if __name__ == "__main__":
    unittest.main()
