"""Upstox, Groww and 5paisa adapters against fake servers that follow each broker's published API.

Each test runs the live flow (practice mode off): login, contract lookup, preview, BUY, fill, protective exit,
exit fill, plus each broker's special case (Upstox GTT bracket, Groww one-day OCO re-placed, 5paisa daily
stop-loss order). No network.
"""
import csv
import gzip
import hashlib
import io
import json
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from stock_agent import broker_base as B
from stock_agent import fivepaisa, groww, upstox

EXPIRY = (date.today() + timedelta(days=25)).isoformat()
PLAN = {"side": "CALL", "expiry": EXPIRY, "strike": 940.0, "entry": 941.85, "premium": 29.9,
        "stop_premium": 21.95, "target1_premium": 42.65, "target2_premium": 53.0,
        "stop_underlying": 927.34, "target1_underlying": 963.61, "target2_underlying": 978.12, "time_stop": EXPIRY}
LOT = 1400


class Fake(BaseHTTPRequestHandler):
    routes = {}
    state = {}

    def log_message(self, *a):
        pass

    def _reply(self, obj, code=200, ctype="application/json"):
        raw = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self, method):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        ctype = self.headers.get("Content-Type", "")
        body = json.loads(raw) if raw and "json" in ctype else ({k: v[0] for k, v in parse_qs(raw.decode()).items()} if raw else {})
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.state.setdefault("calls", []).append((method, u.path, body, dict(self.headers)))
        for (m, prefix), fn in self.routes.items():
            if m == method and (u.path == prefix or (prefix.endswith("/") and u.path.startswith(prefix))):
                out = fn(self.state, u.path, q, body)
                return self._reply(*out) if isinstance(out, tuple) else self._reply(out)
        return self._reply({"error": "no route " + u.path}, 404)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")


def serve(routes):
    cls = type("F", (Fake,), {"routes": routes, "state": {}})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, cls, f"http://127.0.0.1:{srv.server_address[1]}"


class FlowBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.patches = [mock.patch("stock_agent.broker_base.market_open", return_value=True)]
        from stock_agent import checklist, tradetest
        self.patches.append(mock.patch.object(tradetest, "load_validation", return_value={
            "test_avg_ret": 0.01, "test_win_rate": 0.55, "test_trades": 1000}))
        self.patches.append(mock.patch.object(checklist, "next_results_date", return_value=None))
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def preview(self, capital=3_000_000):          # 2% risk allows several lots, so exits split in two
        return B.preview(self.b, self.home, "HINDALCO.NS", PLAN, capital, 0.02)

    def place(self, pv):
        return B.place(self.b, pv, wait_seconds=1, confirmed=["liquid", "events"])


# --------------------------------------------------------------------------- Upstox
def upstox_routes():
    exp_ms = int(datetime.fromisoformat(EXPIRY + "T23:59:59+05:30").timestamp() * 1000)
    rows = [{"segment": "NSE_FO", "instrument_type": "CE", "underlying_symbol": "HINDALCO", "expiry": exp_ms,
             "strike_price": 940.0, "lot_size": LOT, "tick_size": 5.0, "instrument_key": "NSE_FO|777",
             "trading_symbol": f"HINDALCO 940 CE {date.fromisoformat(EXPIRY):%d %b %y}".upper()},
            {"segment": "NSE_FO", "instrument_type": "PE", "underlying_symbol": "HINDALCO", "expiry": exp_ms,
             "strike_price": 940.0, "lot_size": LOT, "tick_size": 5.0, "instrument_key": "NSE_FO|778", "trading_symbol": "X"},
            {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "HINDALCO", "instrument_key": "NSE_EQ|INE038A01020",
             "lot_size": 1, "tick_size": 5.0}]
    gz = gzip.compress(json.dumps(rows).encode())

    def token(s, path, q, b):
        assert b["grant_type"] == "authorization_code" and b["code"] == "code1" and b["client_secret"] == "sec"
        return {"access_token": "tok", "user_name": "Asha"}

    def quotes(s, path, q, b):
        return {"status": "success", "data": {
            "NSE_FO:HINDALCO": {"instrument_token": "NSE_FO|777", "last_price": s["ltp"],
                                "depth": {"buy": [{"price": s["ltp"] - 0.1}], "sell": [{"price": s["ltp"]}]}},
            "NSE_EQ:HINDALCO": {"instrument_token": "NSE_EQ|INE038A01020", "last_price": 941.85}}}

    def gtt_place(s, path, q, b):
        gid = f"GTT-{len(s['gtts']) + 1}"
        eid = f"E{len(s['orders']) + 1}"
        s["orders"][eid] = {"status": "complete", "filled_quantity": b["quantity"],
                            "average_price": b["rules"][0]["trigger_price"]}
        s["gtts"][gid] = {"body": b, "rules": [
            {"strategy": "ENTRY", "status": "COMPLETED", "order_id": eid},
            {"strategy": "TARGET", "status": "SCHEDULED", "order_id": None},
            {"strategy": "STOPLOSS", "status": "SCHEDULED", "order_id": None}]}
        return {"status": "success", "data": {"gtt_order_ids": [gid]}}

    def gtt_get(s, path, q, b):
        g = s["gtts"].get(q["gtt_order_id"])
        return {"status": "success", "data": [] if not g or g.get("gone") else [{"rules": g["rules"]}]}

    def gtt_cancel(s, path, q, b):
        for r in s["gtts"][b["gtt_order_id"]]["rules"][1:]:
            if r["status"] == "SCHEDULED":
                r["status"] = "CANCELLED"
        s["cancelled"].append(b["gtt_order_id"])
        return {"status": "success", "data": {}}

    def order_place(s, path, q, b):
        oid = f"O{len(s['orders']) + 1}"
        s["orders"][oid] = {"status": "complete", "filled_quantity": b["quantity"], "average_price": b["price"], "body": b}
        return {"status": "success", "data": {"order_ids": [oid]}}

    return {
        ("GET", "/instruments.json.gz"): lambda s, p, q, b: (gz, 200, "application/gzip"),
        ("POST", "/v2/login/authorization/token"): token,
        ("GET", "/v3/market-quote/quotes"): quotes,
        ("GET", "/v2/user/get-funds-and-margin"): lambda s, p, q, b: {"status": "success", "data": {"equity": {"available_margin": 900000}}},
        ("POST", "/v3/order/gtt/place"): gtt_place,
        ("GET", "/v3/order/gtt"): gtt_get,
        ("DELETE", "/v3/order/gtt/cancel"): gtt_cancel,
        ("POST", "/v3/order/place"): order_place,
        ("GET", "/v2/order/details"): lambda s, p, q, b: {"status": "success", "data": s["orders"][q["order_id"]]},
    }


class UpstoxTests(FlowBase):
    def setUp(self):
        super().setUp()
        self.srv, self.F, root = serve(upstox_routes())
        self.F.state.update(ltp=29.9, orders={}, gtts={}, cancelled=[])
        for name, val in (("API_ROOT", root), ("HFT_ROOT", root), ("INSTRUMENTS_URL", root + "/instruments.json.gz")):
            p = mock.patch.object(upstox, name, val)
            p.start()
            self.patches.append(p)
        self.b = upstox.Upstox(self.home)
        self.b.update({"api_key": "key", "api_secret": "sec"}, practice=False)

    def test_login_url_and_token(self):
        url = self.b.login_url("http://127.0.0.1:8765/broker/callback/upstox")
        self.assertIn("response_type=code", url)
        self.assertIn("redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2Fbroker%2Fcallback%2Fupstox", url)
        st = self.b.complete_login("code1")
        self.assertTrue(st["connected"])
        self.assertEqual(st["user"], "Asha")

    def test_bracket_gtt_flow(self):
        self.b.complete_login("code1")
        pv = self.preview()
        self.assertEqual(pv["contract"]["instrument_key"], "NSE_FO|777")
        self.assertEqual(pv["contract"]["tick_size"], 0.05)              # Upstox lists 5 paise
        self.assertEqual(pv["limit"], 29.9)
        self.assertGreaterEqual(pv["lots"], 2)
        rec = self.place(pv)
        self.assertTrue(rec["bracket"])
        self.assertEqual(len(rec["gtt_ids"]), 2)                         # one GTT per exit leg
        body = self.F.state["gtts"]["GTT-1"]["body"]
        self.assertEqual([r["strategy"] for r in body["rules"]], ["ENTRY", "TARGET", "STOPLOSS"])
        self.assertTrue(all(r["trigger_type"] == "IMMEDIATE" for r in body["rules"]))
        self.assertEqual(body["product"], "D")
        self.assertEqual(rec["buy_status"], "COMPLETE")
        self.assertEqual(rec["filled_qty"], pv["qty"])
        # target of the first GTT fills: recorded, and the remaining stop leg is cancelled
        tgt = self.F.state["gtts"]["GTT-1"]["rules"][1]
        self.F.state["orders"]["T1"] = {"status": "complete", "filled_quantity": rec["exit_legs"][0]["qty"], "average_price": 43.0}
        tgt.update(status="COMPLETED", order_id="T1")
        B.sync_one(self.b, rec)
        self.assertEqual(rec["exits"][0]["price"], 43.0)
        self.assertIn("GTT-1", self.F.state["cancelled"])
        B.sync_one(self.b, rec)
        self.assertEqual(len(rec["exits"]), 1)                           # not counted twice
        # exit now: cancels the GTTs and sells the rest with a LIMIT order
        B.exit_now(self.b, rec)
        sell = [o for o in self.F.state["orders"].values() if o.get("body", {}).get("transaction_type") == "SELL"][0]
        self.assertEqual(sell["body"]["order_type"], "LIMIT")
        self.assertEqual(sell["body"]["quantity"], pv["qty"] - rec["exit_legs"][0]["qty"])

    def test_finished_gtt_is_reported_once(self):
        self.b.complete_login("code1")
        rec = self.place(self.preview())
        self.F.state["gtts"]["GTT-1"]["gone"] = True
        B.sync_one(self.b, rec)
        B.sync_one(self.b, rec)
        self.assertEqual(sum("no longer lists GTT GTT-1" in e for e in rec["events"]), 1)

    def test_practice_sends_nothing(self):
        self.b.complete_login("code1")
        self.b.update({}, practice=True)
        rec = B.place(self.b, self.preview())
        self.assertTrue(rec["practice"])
        self.assertEqual(self.F.state["gtts"], {})


# --------------------------------------------------------------------------- Groww
def groww_routes():
    rows = [{"exchange": "NSE", "exchange_token": "1", "trading_symbol": "HINDALCO26OCT940CE", "instrument_type": "CE",
             "segment": "FNO", "underlying_symbol": "HINDALCO", "expiry_date": EXPIRY, "strike_price": "940",
             "lot_size": str(LOT), "tick_size": "0.05"},
            {"exchange": "NSE", "exchange_token": "2", "trading_symbol": "HINDALCO", "instrument_type": "EQ",
             "segment": "CASH", "underlying_symbol": "", "expiry_date": "", "strike_price": "", "lot_size": "1",
             "tick_size": "0.05"}]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    csv_bytes = buf.getvalue().encode()

    def token(s, path, q, b):
        ok = b["checksum"] == hashlib.sha256(("sec" + b["timestamp"]).encode()).hexdigest()
        return ({"token": "gtok", "sessionName": "s1"}, 200) if ok else ({"error": "bad checksum"}, 401)

    def quote(s, path, q, b):
        px = s["ltp"] if q["segment"] == "FNO" else 941.85
        return {"status": "SUCCESS", "payload": {"last_price": px, "bid_price": px - 0.1, "offer_price": px}}

    def create(s, path, q, b):
        oid = f"G{len(s['orders']) + 1}"
        s["orders"][oid] = {"groww_order_id": oid, "order_status": "EXECUTED", "quantity": b["quantity"],
                            "filled_quantity": b["quantity"], "average_fill_price": b["price"],
                            "trading_symbol": b["trading_symbol"], "transaction_type": b["transaction_type"], "body": b}
        return {"status": "SUCCESS", "payload": {"groww_order_id": oid, "order_status": "OPEN"}}

    def smart(s, path, q, b):
        sid = f"S{len(s['smart']) + 1}"
        s["smart"][sid] = {"status": "ACTIVE", "body": b}
        return {"status": "SUCCESS", "payload": {"smart_order_id": sid, "status": "ACTIVE"}}

    def smart_cancel(s, path, q, b):
        s["smart"][path.rsplit("/", 1)[-1]]["status"] = "CANCELLED"
        return {"status": "SUCCESS", "payload": {}}

    return {
        ("GET", "/instrument.csv"): lambda s, p, q, b: (csv_bytes, 200, "text/csv"),
        ("POST", "/v1/token/api/access"): token,
        ("GET", "/v1/live-data/quote"): quote,
        ("GET", "/v1/margins/detail/user"): lambda s, p, q, b: {"status": "SUCCESS", "payload": {"clear_cash": 900000}},
        ("POST", "/v1/order/create"): create,
        ("GET", "/v1/order/detail/"): lambda s, p, q, b: {"status": "SUCCESS", "payload": s["orders"][p.rsplit("/", 1)[-1]]},
        ("GET", "/v1/order/list"): lambda s, p, q, b: {"status": "SUCCESS", "payload": {"order_list": list(s["orders"].values())}},
        ("POST", "/v1/order-advance/create"): smart,
        ("GET", "/v1/order-advance/status/FNO/OCO/internal/"): lambda s, p, q, b: {
            "status": "SUCCESS", "payload": {"status": s["smart"][p.rsplit("/", 1)[-1]]["status"]}},
        ("POST", "/v1/order-advance/cancel/FNO/OCO/"): smart_cancel,
    }


class GrowwTests(FlowBase):
    def setUp(self):
        super().setUp()
        self.srv, self.F, root = serve(groww_routes())
        self.F.state.update(ltp=29.9, orders={}, smart={})
        for name, val in (("API_ROOT", root + "/v1"), ("INSTRUMENTS_URL", root + "/instrument.csv")):
            p = mock.patch.object(groww, name, val)
            p.start()
            self.patches.append(p)
        self.b = groww.Groww(self.home)
        self.b.update({"api_key": "gkey", "api_secret": "sec"}, practice=False)
        self.b.direct_login()

    def test_direct_login_and_headers(self):
        self.assertTrue(self.b.status()["connected"])
        self.preview()
        h = [c for c in self.F.state["calls"] if c[1] == "/v1/live-data/quote"][0][3]
        self.assertEqual(h["Authorization"], "Bearer gtok")
        self.assertEqual(h["X-API-VERSION"], "1.0")

    def test_buy_then_oco_and_exit(self):
        pv = self.preview()
        self.assertTrue(any("lasts one day" in w for w in pv["warnings"]))
        rec = self.place(pv)
        buy = self.F.state["orders"]["G1"]["body"]
        self.assertEqual((buy["segment"], buy["product"], buy["order_type"]), ("FNO", "NRML", "LIMIT"))
        self.assertTrue(8 <= len(buy["order_reference_id"]) <= 20)
        self.assertEqual(rec["buy_status"], "COMPLETE")
        self.assertEqual(len(rec["gtt_ids"]), 2)
        oco = self.F.state["smart"]["S1"]["body"]
        self.assertEqual(oco["smart_order_type"], "OCO")
        self.assertEqual(oco["net_position_quantity"], oco["quantity"])
        self.assertEqual(oco["stop_loss"]["order_type"], "SL_M")
        # the OCO triggers: the executed SELL is recorded as the exit
        self.F.state["smart"]["S1"]["status"] = "TRIGGERED"
        self.F.state["orders"]["G9"] = {"groww_order_id": "G9", "order_status": "EXECUTED", "quantity": oco["quantity"],
                                        "filled_quantity": oco["quantity"], "average_fill_price": 42.7,
                                        "trading_symbol": oco["trading_symbol"], "transaction_type": "SELL"}
        B.sync_one(self.b, rec)
        self.assertEqual([(e["order_id"], e["price"]) for e in rec["exits"]], [("G9", 42.7)])

    def test_expired_oco_is_placed_again_once_a_day(self):
        rec = self.place(self.preview())
        self.F.state["smart"]["S1"]["status"] = "EXPIRED"
        B.sync_one(self.b, rec)
        self.assertEqual(rec["gtt_ids"][0], "S3")                       # S1 replaced by a new OCO
        self.F.state["smart"]["S3"]["status"] = "EXPIRED"
        B.sync_one(self.b, rec)
        self.assertEqual(len(self.F.state["smart"]), 3)                 # not again the same day


# --------------------------------------------------------------------------- 5paisa
def fivepaisa_routes():
    rows = [{"Exch": "N", "ExchType": "D", "ScripCode": "36342", "Name": f"HINDALCO {date.fromisoformat(EXPIRY):%d %b %Y} CE 940.00".upper(),
             "Expiry": EXPIRY, "ScripType": "CE", "StrikeRate": "940", "TickSize": "0.05", "LotSize": str(LOT),
             "SymbolRoot": "HINDALCO"}]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    csv_bytes = buf.getvalue().encode()

    def token(s, path, q, b):
        assert b["head"]["Key"] == "ukey" and b["body"] == {"RequestToken": "rt1", "EncryKey": "enc", "UserId": "uid"}
        return {"head": {"Status": 0}, "body": {"AccessToken": "ptok", "ClientCode": "C123", "ClientName": "Ravi",
                                                "Status": 0, "Message": "Success"}}

    def feed(s, path, q, b):
        assert b["body"]["ClientCode"] == "C123"
        return {"head": {"status": "0"}, "body": {"Status": 0, "Data": [
            {"Token": 36342, "LastRate": s["ltp"], "ExchType": "D"}, {"Token": 1363, "LastRate": 941.85, "ExchType": "C"}]}}

    def place(s, path, q, b):
        o = b["body"]
        s["orders"][o["RemoteOrderID"]] = {"Status": "Fully Executed" if not o["StopLossPrice"] else "Pending",
                                           "TradedQty": o["Qty"] if not o["StopLossPrice"] else 0,
                                           "AveragePrice": o["Price"], "ExchOrderID": "1100" + str(len(s["orders"])), "body": o}
        return {"head": {"status": "0"}, "body": {"Status": 0, "BrokerOrderID": 1, "Message": "Success"}}

    def status(s, path, q, b):
        rid = b["body"]["OrdStatusReqList"][0]["RemoteOrderID"]
        row = s["orders"].get(rid)
        return {"head": {"status": "0"}, "body": {"Status": 0, "OrdStatusResLst": [row] if row and not row.get("gone") else []}}

    def cancel(s, path, q, b):
        s["cancelled"].append(b["body"]["ExchOrderID"])
        return {"head": {"status": "0"}, "body": {"Status": 0}}

    return {
        ("GET", "/ScripMaster/segment/nse_fo"): lambda s, p, q, b: (csv_bytes, 200, "text/csv"),
        ("POST", "/GetAccessToken"): token,
        ("POST", "/V1/MarketFeed"): feed,
        ("POST", "/V1/PlaceOrderRequest"): place,
        ("POST", "/V3/OrderStatus"): status,
        ("POST", "/V1/CancelOrderRequest"): cancel,
    }


class FivePaisaTests(FlowBase):
    def setUp(self):
        super().setUp()
        self.srv, self.F, root = serve(fivepaisa_routes())
        self.F.state.update(ltp=29.9, orders={}, cancelled=[])
        p = mock.patch.object(fivepaisa, "API_ROOT", root)
        p.start()
        self.patches.append(p)
        self.b = fivepaisa.FivePaisa(self.home)
        self.b.update({"api_key": "ukey", "user_id": "uid", "api_secret": "enc"}, practice=False)
        self.b.complete_login("rt1")

    def test_login(self):
        st = self.b.status()
        self.assertTrue(st["connected"])
        self.assertEqual(self.b.cfg["client_code"], "C123")
        self.assertIn("VendorKey=ukey", self.b.login_url("http://127.0.0.1:8765/broker/callback/fivepaisa"))

    def test_buy_then_daily_stop_loss(self):
        pv = self.preview()
        self.assertEqual(pv["contract"]["scrip_code"], 36342)
        self.assertTrue(any("no stop + target order" in w for w in pv["warnings"]))
        rec = self.place(pv)
        orders = list(self.F.state["orders"].values())
        buy = orders[0]["body"]
        self.assertEqual((buy["OrderType"], buy["ExchangeType"], buy["IsIntraday"], buy["StopLossPrice"]), ("Buy", "D", False, 0.0))
        self.assertEqual(rec["buy_status"], "COMPLETE")
        stops = [o["body"] for o in orders[1:]]
        self.assertEqual(len(stops), 2)
        for s in stops:
            self.assertEqual(s["OrderType"], "Sell")
            self.assertAlmostEqual(s["StopLossPrice"], pv["levels"]["stop"])
            self.assertLess(s["Price"], s["StopLossPrice"])                # limit below the trigger
        # next day the day orders are gone: placed again, once
        for rid in rec["gtt_ids"]:
            self.F.state["orders"][rid]["gone"] = True
        B.sync_one(self.b, rec)
        self.assertEqual(len(self.F.state["orders"]), 5)
        B.sync_one(self.b, rec)
        self.assertEqual(len(self.F.state["orders"]), 5)
        # a stop fills: recorded as the exit
        rid = rec["gtt_ids"][0]
        self.F.state["orders"][rid].update(Status="Fully Executed", TradedQty=rec["exit_legs"][0]["qty"], AveragePrice=21.0)
        B.sync_one(self.b, rec)
        self.assertEqual([(e["order_id"], e["price"]) for e in rec["exits"]], [(rid, 21.0)])
        # exit now cancels the open stop order by its exchange id and sells the rest
        B.exit_now(self.b, rec)
        self.assertEqual(len(self.F.state["cancelled"]), 1)


if __name__ == "__main__":
    unittest.main()


class AppBrokerTests(unittest.TestCase):
    def test_select_status_and_callback_state(self):
        import urllib.error
        import urllib.request

        from stock_agent import app

        tmp = tempfile.TemporaryDirectory()
        home = Path(tmp.name)
        with mock.patch.object(app, "HOME", home), mock.patch.object(app, "SETTINGS_FILE", home / "settings.json"), \
                mock.patch.dict(app._brokers, clear=True):
            srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{srv.server_address[1]}"

            def call(path, body=None, token=True):
                req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                             headers={"X-Agent-Token": app.SESSION_TOKEN if token else "",
                                                      "Content-Type": "application/json"})
                with urllib.request.urlopen(req) as r:
                    raw = r.read()
                    return json.loads(raw) if r.headers.get_content_type() == "application/json" else raw.decode()
            try:
                st = call("/api/kite/status")
                self.assertEqual(st["broker"], "kite")
                self.assertEqual(set(st["brokers"]), {"kite", "upstox", "groww", "fivepaisa"})
                st = call("/api/kite/select", {"broker": "upstox"})
                self.assertEqual(st["label"], "Upstox")
                st = call("/api/kite/status")
                self.assertTrue(st["redirect_url"].endswith("/broker/callback/upstox"))
                self.assertTrue(any(st["redirect_url"] in x for x in st["setup"]))
                call("/api/kite/config", {"fields": {"api_key": "k", "api_secret": "s"}, "practice": True})
                url = call("/api/kite/login")["url"]
                state = parse_qs(urlparse(url).query)["state"][0]
                page = call("/broker/callback/upstox?code=abc&state=wrong", token=False)
                self.assertIn("did not start from this app", page)
                self.assertTrue(state)
                with self.assertRaises(urllib.error.HTTPError):
                    call("/api/kite/select", {"broker": "nope"})
            finally:
                srv.shutdown()
                srv.server_close()
                tmp.cleanup()
