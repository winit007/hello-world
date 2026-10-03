"""Zerodha Kite Connect adapter (REST API v3, no extra libraries).

The order flow itself (preview, LIMIT BUY, protective exits once filled, sync, exit now) lives in
broker_base and is shared by every broker. Kite protects a filled position with a two-leg GTT (OCO):
one leg sells at the stop, the other at the target, and whichever triggers first cancels the other.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from datetime import date
from pathlib import Path

import requests

from .broker_base import (IST, TICK, Broker, BrokerError, exit_now, floor_tick, market_open, now_ist, place,  # noqa: F401
                          preview, sync_one, tick)

API_ROOT = os.environ.get("KITE_API_ROOT", "https://api.kite.trade")
LOGIN_URL = "https://kite.zerodha.com/connect/login?v=3&api_key={key}"
PRODUCT = "NRML"           # carry-forward F&O position (the plan holds for days)
KiteError = BrokerError    # kept for callers written before the other brokers


class Kite(Broker):
    key = "kite"
    label = "Kite"
    FIELDS = [{"name": "api_key", "label": "API key", "help": "From developers.kite.trade > your app"},
              {"name": "api_secret", "label": "API secret", "secret": True}]
    SETUP = ["Go to <a href=\"https://developers.kite.trade\" target=\"_blank\" rel=\"noopener\">developers.kite.trade</a>, sign up and "
             "create an app (check Zerodha's current pricing; live quotes need the paid Connect plan).",
             "Set the app's <b>Redirect URL</b> to <b>{redirect}</b>.",
             "Copy the <b>API key</b> and <b>API secret</b> below and save.",
             "Each morning press <b>Log in</b>. Kite access expires every day at 6 am; that is Zerodha's rule.",
             "If Kite rejects orders mentioning an IP address, register your static IP in the Kite developer console "
             "(SEBI rule for API orders)."]
    EXPIRES_AT = (6, 0)
    LOGIN_PARAM = "request_token"

    def _load(self) -> dict:
        try:
            return json.loads(self.cfg_path.read_text(encoding="utf-8"))
        except Exception:
            return {"api_key": "", "api_secret": "", "practice": True}

    def update_config(self, api_key=None, api_secret=None, practice=None) -> dict:
        return self.update({"api_key": api_key, "api_secret": api_secret or ""}, practice)

    def login_url(self, redirect: str | None = None) -> str:
        if not self.cfg.get("api_key"):
            raise KiteError("Add your Kite API key in Settings first")
        return LOGIN_URL.format(key=self.cfg["api_key"])

    def complete_login(self, request_token: str) -> dict:
        key, secret = self.cfg.get("api_key"), self.cfg.get("api_secret")
        if not (key and secret):
            raise KiteError("API key and secret are not set")
        checksum = hashlib.sha256((key + request_token + secret).encode()).hexdigest()
        data = self._call("POST", "/session/token", data={"api_key": key, "request_token": request_token,
                                                          "checksum": checksum}, auth=False)
        self._store_token(data["access_token"], data.get("user_name") or data.get("user_id"))
        return self.status()

    # -- HTTP
    def _headers(self, auth=True) -> dict:
        h = {"X-Kite-Version": "3", "User-Agent": "stock-agent"}
        if auth:
            if not self.token_valid():
                raise KiteError("Not logged in to Kite today. Press 'Log in' in Settings.")
            h["Authorization"] = f"token {self.cfg['api_key']}:{self.cfg['access_token']}"
        return h

    def _call(self, method: str, path: str, params=None, data=None, auth=True, raw=False):
        try:
            r = requests.request(method, API_ROOT + path, params=params, data=data, headers=self._headers(auth), timeout=20)
        except requests.RequestException as exc:
            raise KiteError(f"Could not reach Kite: {exc}") from exc
        if raw and r.ok:
            return r.text
        try:
            body = r.json()
        except ValueError:
            raise KiteError(f"Kite returned HTTP {r.status_code}") from None
        if r.status_code >= 400 or body.get("status") == "error":
            msg = body.get("message") or f"HTTP {r.status_code}"
            if body.get("error_type") == "TokenException":
                self.cfg.pop("access_token", None)
                self.save()
                msg += " (please log in to Kite again)"
            raise KiteError(msg)
        return body.get("data")

    # -- market data
    def instruments(self, cache_dir: Path) -> list[dict]:
        path = cache_dir / "kite_nfo.csv"
        if not (path.exists() and date.fromtimestamp(path.stat().st_mtime) == date.today()):
            text = self._call("GET", "/instruments/NFO", raw=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))

    def find_contract(self, cache_dir: Path, symbol: str, expiry: date, kind: str, strike: float, spot: float) -> dict:
        """Exact NFO option for the plan; falls back to the listed strike nearest the spot."""
        want_type = "CE" if kind == "call" else "PE"
        rows = [r for r in self.instruments(cache_dir)
                if r.get("name") == symbol and r.get("instrument_type") == want_type and r.get("segment") == "NFO-OPT"]
        if not rows:
            raise KiteError(f"{symbol} has no options on NSE F&O")
        same_exp = [r for r in rows if r["expiry"] == expiry.isoformat()]
        if not same_exp:  # plan's expiry not listed: take the nearest listed one after it
            later = sorted({r["expiry"] for r in rows if r["expiry"] >= expiry.isoformat()})
            if not later:
                raise KiteError(f"No {symbol} option expiry on or after {expiry}")
            same_exp = [r for r in rows if r["expiry"] == later[0]]
        exact = [r for r in same_exp if abs(float(r["strike"]) - strike) < 1e-6]
        row = exact[0] if exact else min(same_exp, key=lambda r: abs(float(r["strike"]) - spot))
        return {"tradingsymbol": row["tradingsymbol"], "lot_size": int(float(row["lot_size"])), "underlying": symbol,
                "strike": float(row["strike"]), "expiry": row["expiry"], "tick_size": float(row.get("tick_size") or TICK)}

    def quote(self, keys: list[str]) -> dict | None:
        """Full quotes, or None when the Kite plan has no market-data access."""
        try:
            return self._call("GET", "/quote", params=[("i", k) for k in keys])
        except KiteError as exc:
            if "permission" in str(exc).lower() or "subscription" in str(exc).lower():
                return None
            raise

    def live(self, c: dict, symbol: str | None) -> dict | None:
        keys = [f"NFO:{c['tradingsymbol']}"] + ([f"NSE:{symbol}"] if symbol else [])
        q = self.quote(keys)
        if not q:
            return None
        oq, sq = q.get(keys[0]) or {}, (q.get(keys[1]) or {}) if symbol else {}
        ask = ((oq.get("depth") or {}).get("sell") or [{}])[0].get("price") or 0
        bid = ((oq.get("depth") or {}).get("buy") or [{}])[0].get("price") or 0
        return {"ltp": float(oq.get("last_price") or 0), "bid": float(bid), "ask": float(ask),
                "spot": float(sq.get("last_price") or 0) or None}

    def funds(self) -> float | None:
        try:
            eq = self._call("GET", "/user/margins/equity")
            return float(eq["available"].get("live_balance", eq.get("net", 0)))
        except Exception:
            return None

    # -- orders
    def place_order(self, c, side: str, qty: int, price: float, tag: str = "stockagent") -> str:
        sym = c["tradingsymbol"] if isinstance(c, dict) else c
        data = self._call("POST", "/orders/regular", data={
            "exchange": "NFO", "tradingsymbol": sym, "transaction_type": side, "quantity": int(qty),
            "product": PRODUCT, "order_type": "LIMIT", "price": f"{price:.2f}", "validity": "DAY", "tag": tag[:20]})
        return str(data["order_id"])

    def order_state(self, order_id: str) -> dict:
        hist = self._call("GET", f"/orders/{order_id}")
        last = hist[-1] if isinstance(hist, list) and hist else (hist or {})
        return {"status": last.get("status"), "filled": int(last.get("filled_quantity") or 0),
                "avg_price": float(last.get("average_price") or 0), "message": last.get("status_message")}

    def place_exit(self, c, qty: int, ltp: float, stop: float, target: float) -> str:
        sym = c["tradingsymbol"] if isinstance(c, dict) else c
        cond = {"exchange": "NFO", "tradingsymbol": sym, "trigger_values": [stop, target], "last_price": ltp}
        legs = [
            # lower trigger = stop-loss: sell a little below the trigger so the order fills in a fast fall
            {"exchange": "NFO", "tradingsymbol": sym, "transaction_type": "SELL", "quantity": int(qty),
             "order_type": "LIMIT", "product": PRODUCT, "price": floor_tick(stop * 0.97)},
            {"exchange": "NFO", "tradingsymbol": sym, "transaction_type": "SELL", "quantity": int(qty),
             "order_type": "LIMIT", "product": PRODUCT, "price": tick(target)},
        ]
        data = self._call("POST", "/gtt/triggers", data={"type": "two-leg", "condition": json.dumps(cond),
                                                          "orders": json.dumps(legs)})
        return str(data["trigger_id"])

    def exit_state(self, trigger_id: str, c=None) -> dict:
        g = self._call("GET", f"/gtt/triggers/{trigger_id}")
        order_ids = []
        for leg in g.get("orders") or []:
            res = (leg.get("result") or {}).get("order_result") or {}
            if res.get("order_id"):
                order_ids.append(str(res["order_id"]))
        return {"status": g.get("status"), "order_ids": order_ids}

    def cancel_exit(self, trigger_id: str, c=None) -> None:
        self._call("DELETE", f"/gtt/triggers/{trigger_id}")

    # names used before the broker layer existed
    place_gtt, gtt_state, cancel_gtt = place_exit, exit_state, cancel_exit
