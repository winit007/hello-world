"""5paisa Xstream Open API adapter, written from 5paisa's published docs.

Login: 5paisa's web login. "Log in" opens it; 5paisa sends a RequestToken back to this app's redirect URL,
which is exchanged (with your User ID and Encryption Key) for the day's token (expires 11:59 pm).

Exit protection: 5paisa's API has no GTT, OCO or bracket orders, and market orders are not accepted. After
the BUY fills, the app places a day stop-loss order (trigger at the stop, limit a little below) and places it
again each trading day while the position is open, whenever it syncs (open My trades). The target is not
automated: sell at the target yourself. 5paisa requires a static IP registered on the Xstream dashboard.
"""
from __future__ import annotations

import csv
import io
import os
import secrets
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import requests

from .broker_base import TICK, Broker, BrokerError, floor_tick, tick

API_ROOT = os.environ.get("FIVEPAISA_API_ROOT", "https://Openapi.5paisa.com/VendorsAPI/Service1.svc")
LOGIN_URL = os.environ.get("FIVEPAISA_LOGIN_URL", "https://dev-openapi.5paisa.com/WebVendorLogin/VLogin/Index")


class FivePaisa(Broker):
    key = "fivepaisa"
    label = "5paisa"
    LOGIN_PARAM = "RequestToken"
    EXPIRES_AT = (23, 59)
    FIELDS = [{"name": "api_key", "label": "User key", "help": "Xstream API dashboard > your app"},
              {"name": "user_id", "label": "User ID", "help": "Xstream API dashboard (not your client code)"},
              {"name": "api_secret", "label": "Encryption key", "secret": True}]
    EXIT_NOTE = ("5paisa's API has no stop + target order: the app places a stop-loss order each trading day when you "
                 "open My trades, and you sell at the target yourself (set a price alert)")
    SETUP = ["Open the <a href=\"https://xstream.5paisa.com\" target=\"_blank\" rel=\"noopener\">5paisa Xstream API "
             "dashboard</a> and create an app.",
             "Set its <b>Response / redirect URL</b> to <b>{redirect}</b> and add your <b>static IP</b> "
             "(5paisa rejects API orders from other IPs).",
             "Copy the <b>User key</b>, <b>User ID</b> and <b>Encryption key</b> below and save.",
             "Each day press <b>Log in</b> (client code, PIN and OTP on 5paisa's page). Access expires at 11:59 pm."]

    def login_url(self, redirect: str | None = None) -> str:
        if not self.cfg.get("api_key"):
            raise BrokerError("Add your 5paisa User key in Settings first")
        return f"{LOGIN_URL}?" + urlencode({"VendorKey": self.cfg["api_key"], "ResponseURL": redirect or ""})

    def complete_login(self, request_token: str) -> dict:
        try:
            r = requests.post(f"{API_ROOT}/GetAccessToken", json={
                "head": {"Key": self.cfg.get("api_key")},
                "body": {"RequestToken": request_token, "EncryKey": self.cfg.get("api_secret"),
                         "UserId": self.cfg.get("user_id")}}, headers={"Content-Type": "application/json"}, timeout=20)
            body = (r.json() or {}).get("body") or {}
        except (requests.RequestException, ValueError) as exc:
            raise BrokerError(f"Could not reach 5paisa: {exc}") from exc
        if not body.get("AccessToken"):
            raise BrokerError(f"5paisa login failed: {body.get('Message') or r.status_code}")
        self.cfg["client_code"] = body.get("ClientCode")
        self._store_token(body["AccessToken"], body.get("ClientName") or body.get("ClientCode"))
        return self.status()

    # -- HTTP: every call is a POST of {"head": {"key"}, "body": {...}}
    def _call(self, path: str, body: dict) -> dict:
        self.need_login()
        try:
            r = requests.post(f"{API_ROOT}/{path}", json={"head": {"key": self.cfg["api_key"]},
                                                         "body": {"ClientCode": self.cfg.get("client_code"), **body}},
                              headers={"Content-Type": "application/json",
                                       "Authorization": f"bearer {self.cfg['access_token']}"}, timeout=20)
            data = r.json()
        except requests.RequestException as exc:
            raise BrokerError(f"Could not reach 5paisa: {exc}") from exc
        except ValueError:
            raise BrokerError(f"5paisa returned HTTP {r.status_code}") from None
        b = data.get("body") or {}
        st = b.get("Status")
        if r.status_code == 429:
            raise BrokerError("5paisa: too many requests, try again in a moment")
        if st == 9 or "invalid session" in str(b.get("Message", "")).lower():
            self.cfg.pop("access_token", None)
            self.save()
            raise BrokerError("5paisa: session expired (log in to 5paisa again)")
        if r.status_code >= 400 or str((data.get("head") or {}).get("status", "0")) not in ("0", "") or st not in (0, None):
            raise BrokerError(f"5paisa: {b.get('Message') or (data.get('head') or {}).get('statusDescription') or r.status_code}")
        return b

    # -- scrip master (NSE derivatives), once a day
    def instruments(self, cache_dir: Path) -> list[dict]:
        path = cache_dir / "fivepaisa_nse_fo.csv"
        if not (path.exists() and date.fromtimestamp(path.stat().st_mtime) == date.today()):
            try:
                text = requests.get(f"{API_ROOT}/ScripMaster/segment/nse_fo", timeout=60).text
            except requests.RequestException as exc:
                raise BrokerError(f"Could not download 5paisa's scrip master: {exc}") from exc
            rows = [r for r in csv.DictReader(io.StringIO(text))
                    if r.get("Exch") == "N" and r.get("ExchType") == "D" and r.get("ScripType") in ("CE", "PE")]
            if not rows:
                raise BrokerError("5paisa's scrip master was empty")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
        with path.open(encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def find_contract(self, cache_dir: Path, symbol: str, expiry: date, kind: str, strike: float, spot: float) -> dict:
        want = "CE" if kind == "call" else "PE"
        rows = [r for r in self.instruments(cache_dir) if r.get("SymbolRoot") == symbol and r.get("ScripType") == want]
        if not rows:
            raise BrokerError(f"{symbol} has no options on NSE F&O")
        same = [r for r in rows if r["Expiry"][:10] == expiry.isoformat()]
        if not same:
            later = sorted({r["Expiry"][:10] for r in rows if r["Expiry"][:10] >= expiry.isoformat()})
            if not later:
                raise BrokerError(f"No {symbol} option expiry on or after {expiry}")
            same = [r for r in rows if r["Expiry"][:10] == later[0]]
        exact = [r for r in same if abs(float(r["StrikeRate"]) - strike) < 1e-6]
        row = exact[0] if exact else min(same, key=lambda r: abs(float(r["StrikeRate"]) - spot))
        return {"tradingsymbol": row["Name"], "scrip_code": int(row["ScripCode"]), "lot_size": int(float(row["LotSize"])),
                "strike": float(row["StrikeRate"]), "expiry": row["Expiry"][:10],
                "tick_size": float(row.get("TickSize") or TICK), "underlying": symbol}

    def live(self, c: dict, symbol: str | None) -> dict | None:
        items = [{"Exch": "N", "ExchType": "D", "ScripCode": str(c["scrip_code"]), "ScripData": ""}]
        if symbol:
            items.append({"Exch": "N", "ExchType": "C", "ScripData": f"{symbol}_EQ"})
        try:
            b = self._call("V1/MarketFeed", {"MarketFeedData": items, "LastRequestTime": "/Date(0)/", "RefreshRate": "H"})
        except BrokerError as exc:
            if "log in" in str(exc):
                raise
            return None
        data = b.get("Data") or []
        opt = next((d for d in data if str(d.get("Token")) == str(c["scrip_code"])), None)
        if not opt:
            return None
        und = next((d for d in data if d is not opt and d.get("ExchType") == "C"), {})
        return {"ltp": float(opt.get("LastRate") or 0), "bid": 0.0, "ask": 0.0,   # the feed carries no bid/ask
                "spot": float(und.get("LastRate") or 0) or None}

    def funds(self) -> float | None:
        return None      # not used: 5paisa's margin call is not part of this integration

    # -- orders: identified by our RemoteOrderID, as 5paisa recommends
    def _send(self, c: dict, side: str, qty: int, price: float, trigger: float = 0.0) -> str:
        rid = "SA" + secrets.token_hex(7)
        self._call("V1/PlaceOrderRequest", {
            "Exchange": "N", "ExchangeType": "D", "ScripCode": int(c["scrip_code"]), "Price": float(price),
            "StopLossPrice": float(trigger), "OrderType": "Buy" if side == "BUY" else "Sell", "Qty": int(qty),
            "DisQty": 0, "IsIntraday": False, "iOrderValidity": 0, "AHPlaced": "N", "RemoteOrderID": rid})
        return rid

    def place_order(self, c: dict, side: str, qty: int, price: float) -> str:
        return self._send(c, side, qty, tick(price, c.get("tick_size") or TICK))

    def _status_row(self, rid: str) -> dict | None:
        b = self._call("V3/OrderStatus", {"OrdStatusReqList": [{"Exch": "N", "RemoteOrderID": rid}]})
        lst = b.get("OrdStatusResLst") or []
        return lst[0] if lst else None

    def order_state(self, rid: str) -> dict:
        row = self._status_row(rid)
        if not row:
            return {"status": "UNKNOWN", "filled": 0, "avg_price": 0.0, "message": "5paisa no longer lists this order"}
        raw = str(row.get("Status") or row.get("OrderStatus") or "")
        low = raw.lower()
        status = ("COMPLETE" if low == "fully executed" else "REJECTED" if "rejected" in low
                  else "CANCELLED" if "cancel" in low else "OPEN")
        return {"status": status, "filled": int(row.get("TradedQty") or 0),
                "avg_price": float(row.get("AveragePrice") or 0), "message": raw}

    def place_exit(self, c: dict, qty: int, ltp: float, stop: float, target: float) -> str:
        step = c.get("tick_size") or TICK
        return self._send(c, "SELL", qty, floor_tick(stop * 0.97, step), tick(stop, step))

    def exit_state(self, rid: str, c: dict | None = None) -> dict:
        st = self.order_state(rid)
        if st["status"] == "COMPLETE":
            return {"status": "COMPLETE", "order_ids": [rid]}
        if st["status"] in ("UNKNOWN", "CANCELLED", "REJECTED"):
            return {"status": "EXPIRED", "order_ids": []}     # a day order from an earlier day, or not accepted
        return {"status": "OPEN", "order_ids": []}

    def cancel_exit(self, rid: str, c: dict | None = None) -> None:
        row = self._status_row(rid)
        if not row or not row.get("ExchOrderID") or str(row.get("ExchOrderID")) == "0":
            return
        if str(row.get("Status") or row.get("OrderStatus") or "").lower() in ("fully executed", "cancelled") \
                or "rejected" in str(row.get("Status") or "").lower():
            return                                   # nothing left to cancel
        self._call("V1/CancelOrderRequest", {"ExchOrderID": str(row["ExchOrderID"])})
