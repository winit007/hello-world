"""Groww Trade API adapter (REST, https://api.groww.in/v1), written from Groww's published docs.

Login: the API key + secret flow. Each day Groww needs the key approved on its API Keys page; then
"Log in" exchanges a checksum of the secret for the day's access token (expires 6 am). Alternatively paste
the day's access token generated on Groww's Trading APIs page.

Exit protection: a Groww OCO smart order (target LIMIT + stop-loss SL-M on the option). Groww documents OCO
orders with a one-day duration, so the app places it again each trading day while the position is open,
whenever it syncs (open My trades). Groww also requires a static IP registered on its API Keys page for
API orders, and a paid Trading API subscription.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import secrets
import time
from datetime import date
from pathlib import Path

import requests

from .broker_base import TICK, Broker, BrokerError, tick

API_ROOT = os.environ.get("GROWW_API_ROOT", "https://api.groww.in/v1")
INSTRUMENTS_URL = os.environ.get("GROWW_INSTRUMENTS_URL", "https://growwapi-assets.groww.in/instruments/instrument.csv")
PRODUCT = "NRML"


def _ref() -> str:
    """8-20 characters, letters/digits and at most two hyphens (Groww's rule for reference ids)."""
    return "sa-" + secrets.token_hex(6)


class Groww(Broker):
    key = "groww"
    label = "Groww"
    LOGIN_KIND = "direct"
    EXPIRES_AT = (6, 0)
    FIELDS = [{"name": "api_key", "label": "API key", "help": "Groww > Trading APIs > API keys"},
              {"name": "api_secret", "label": "API secret", "secret": True},
              {"name": "access_token", "label": "Or today's access token", "secret": True, "optional": True,
               "help": "Paste instead of key approval if you generated one on Groww's Trading APIs page"}]
    EXIT_NOTE = ("Groww's stop/target (OCO) order lasts one day: open My trades each trading day so the app places it "
                 "again, or the position is unprotected")
    SETUP = ["Subscribe to Groww's Trading API at <a href=\"https://groww.in/user/profile/trading-apis\" target=\"_blank\" "
             "rel=\"noopener\">groww.in › Profile › Trading APIs</a> (Groww charges a monthly fee).",
             "On the <a href=\"https://groww.in/trade-api/api-keys\" target=\"_blank\" rel=\"noopener\">API Keys page</a> "
             "create an API key and secret, and add your <b>static IP</b> (Groww rejects API orders from other IPs).",
             "Paste the key and secret below and save.",
             "Each morning approve the key on Groww's API Keys page, then press <b>Log in</b> here. Access expires at 6 am."]

    def configured(self) -> bool:
        return bool(self.cfg.get("api_key") and self.cfg.get("api_secret")) or bool(self.cfg.get("pasted_token"))

    def update(self, values: dict, practice=None) -> dict:
        pasted = (values.get("access_token") or "").strip()
        values = {k: v for k, v in values.items() if k != "access_token"}
        st = super().update(values, practice)
        if pasted:
            self.cfg["pasted_token"] = pasted
            self._store_token(pasted, "Groww (pasted token)")
            st = self.status()
        return st

    # -- login: no browser redirect; the key must be approved on Groww's site first
    def direct_login(self) -> dict:
        key, secret = self.cfg.get("api_key"), self.cfg.get("api_secret")
        if not (key and secret):
            raise BrokerError("Add your Groww API key and secret in Settings first")
        ts = str(int(time.time()))
        checksum = hashlib.sha256((secret + ts).encode("utf-8")).hexdigest()
        try:
            r = requests.post(f"{API_ROOT}/token/api/access", json={"key_type": "approval", "checksum": checksum,
                                                                     "timestamp": ts},
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                       "Accept": "application/json"}, timeout=20)
            body = r.json()
        except (requests.RequestException, ValueError) as exc:
            raise BrokerError(f"Could not reach Groww: {exc}") from exc
        token = body.get("token") or (body.get("payload") or {}).get("token")
        if r.status_code >= 400 or not token:
            err = (body.get("error") or {}).get("message") or body.get("message") or f"HTTP {r.status_code}"
            raise BrokerError(f"Groww login failed: {err}. Approve the API key on Groww's API Keys page first.")
        self._store_token(token, body.get("sessionName") or "Groww")
        return self.status()

    # -- HTTP
    def _headers(self) -> dict:
        self.need_login()
        return {"Authorization": f"Bearer {self.cfg['access_token']}", "Accept": "application/json",
                "X-API-VERSION": "1.0", "Content-Type": "application/json"}

    def _call(self, method: str, path: str, params=None, body=None):
        try:
            r = requests.request(method, API_ROOT + path, params=params, json=body, headers=self._headers(), timeout=20)
        except requests.RequestException as exc:
            raise BrokerError(f"Could not reach Groww: {exc}") from exc
        try:
            data = r.json()
        except ValueError:
            raise BrokerError(f"Groww returned HTTP {r.status_code}") from None
        if r.status_code >= 400 or data.get("status") == "FAILURE":
            err = data.get("error") or {}
            msg = err.get("message") or data.get("message") or f"HTTP {r.status_code}"
            if r.status_code in (401, 403) or err.get("code") == "GA005":
                self.cfg.pop("access_token", None)
                self.save()
                msg += " (log in to Groww again)"
            raise BrokerError(f"Groww: {msg}")
        return data.get("payload")

    # -- instruments: Groww's full list is ~19 MB; keep just NSE options and equities, once a day
    def instruments(self, cache_dir: Path) -> list[dict]:
        path = cache_dir / "groww_nse.csv"
        if not (path.exists() and date.fromtimestamp(path.stat().st_mtime) == date.today()):
            try:
                text = requests.get(INSTRUMENTS_URL, timeout=60).text
            except requests.RequestException as exc:
                raise BrokerError(f"Could not download Groww's instrument list: {exc}") from exc
            rows = [r for r in csv.DictReader(io.StringIO(text))
                    if r.get("exchange") == "NSE" and (r.get("instrument_type") in ("CE", "PE")
                                                       or (r.get("segment") == "CASH" and r.get("instrument_type") == "EQ"))]
            if not rows:
                raise BrokerError("Groww's instrument list was empty")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
        with path.open(encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def find_contract(self, cache_dir: Path, symbol: str, expiry: date, kind: str, strike: float, spot: float) -> dict:
        want = "CE" if kind == "call" else "PE"
        rows = [r for r in self.instruments(cache_dir)
                if r.get("underlying_symbol") == symbol and r.get("instrument_type") == want and r.get("segment") == "FNO"]
        if not rows:
            raise BrokerError(f"{symbol} has no options on NSE F&O")
        same = [r for r in rows if r["expiry_date"] == expiry.isoformat()]
        if not same:
            later = sorted({r["expiry_date"] for r in rows if r["expiry_date"] >= expiry.isoformat()})
            if not later:
                raise BrokerError(f"No {symbol} option expiry on or after {expiry}")
            same = [r for r in rows if r["expiry_date"] == later[0]]
        exact = [r for r in same if abs(float(r["strike_price"]) - strike) < 1e-6]
        row = exact[0] if exact else min(same, key=lambda r: abs(float(r["strike_price"]) - spot))
        return {"tradingsymbol": row["trading_symbol"], "lot_size": int(float(row["lot_size"])), "underlying": symbol,
                "strike": float(row["strike_price"]), "expiry": row["expiry_date"],
                "tick_size": float(row.get("tick_size") or TICK), "segment": "FNO"}

    def live(self, c: dict, symbol: str | None) -> dict | None:
        try:
            q = self._call("GET", "/live-data/quote", params={"exchange": "NSE", "segment": "FNO",
                                                              "trading_symbol": c["tradingsymbol"]}) or {}
            spot = None
            if symbol:
                sq = self._call("GET", "/live-data/quote", params={"exchange": "NSE", "segment": "CASH",
                                                                   "trading_symbol": symbol}) or {}
                spot = float(sq.get("last_price") or 0) or None
        except BrokerError as exc:
            if "log in" in str(exc):
                raise
            return None
        depth = q.get("depth") or {}
        bid = q.get("bid_price") or ((depth.get("buy") or [{}])[0].get("price"))
        ask = q.get("offer_price") or ((depth.get("sell") or [{}])[0].get("price"))
        return {"ltp": float(q.get("last_price") or 0), "bid": float(bid or 0), "ask": float(ask or 0), "spot": spot}

    def funds(self) -> float | None:
        try:
            m = self._call("GET", "/margins/detail/user") or {}
        except BrokerError:
            return None
        for k in ("clear_cash", "available_margin", "net_margin_available"):   # field name not documented
            if isinstance(m.get(k), (int, float)):
                return float(m[k])
        return None

    # -- orders
    def place_order(self, c: dict, side: str, qty: int, price: float) -> str:
        p = self._call("POST", "/order/create", body={
            "trading_symbol": c["tradingsymbol"], "quantity": int(qty), "price": tick(price, c.get("tick_size") or TICK),
            "validity": "DAY", "exchange": "NSE", "segment": "FNO", "product": PRODUCT, "order_type": "LIMIT",
            "transaction_type": side, "order_reference_id": _ref()})
        return str(p["groww_order_id"])

    def order_state(self, order_id: str) -> dict:
        d = self._call("GET", f"/order/detail/{order_id}", params={"segment": "FNO"}) or {}
        raw = (d.get("order_status") or "").upper()
        filled, qty = int(d.get("filled_quantity") or 0), int(d.get("quantity") or 0)
        if raw in ("EXECUTED", "COMPLETED") and (not qty or filled >= qty):
            status = "COMPLETE"
        elif raw in ("REJECTED", "FAILED"):
            status = "REJECTED"
        elif raw == "CANCELLED":
            status = "CANCELLED"
        else:
            status = "OPEN"
        return {"status": status, "filled": filled, "avg_price": float(d.get("average_fill_price") or 0),
                "message": d.get("remark")}

    def place_exit(self, c: dict, qty: int, ltp: float, stop: float, target: float) -> str:
        step = c.get("tick_size") or TICK
        p = self._call("POST", "/order-advance/create", body={
            "reference_id": _ref(), "smart_order_type": "OCO", "segment": "FNO", "trading_symbol": c["tradingsymbol"],
            "quantity": int(qty), "net_position_quantity": int(qty), "transaction_type": "SELL",
            "target": {"trigger_price": f"{tick(target, step):.2f}", "order_type": "LIMIT", "price": f"{tick(target, step):.2f}"},
            "stop_loss": {"trigger_price": f"{tick(stop, step):.2f}", "order_type": "SL_M", "price": None},
            "product_type": PRODUCT, "exchange": "NSE", "duration": "DAY"})
        return str(p["smart_order_id"])

    def exit_state(self, smart_id: str, c: dict | None = None) -> dict:
        g = self._call("GET", f"/order-advance/status/FNO/OCO/internal/{smart_id}") or {}
        status = (g.get("status") or g.get("smart_order_status") or "").upper()
        order_ids = []
        if status in ("TRIGGERED", "COMPLETED") and c:
            # the docs don't name the triggered child order, so find executed SELLs of this contract
            lst = (self._call("GET", "/order/list", params={"segment": "FNO", "page": 0, "page_size": 100}) or {})
            for o in lst.get("order_list") or []:
                if (o.get("trading_symbol") == c["tradingsymbol"] and o.get("transaction_type") == "SELL"
                        and (o.get("order_status") or "").upper() in ("EXECUTED", "COMPLETED")):
                    order_ids.append(str(o["groww_order_id"]))
        return {"status": status, "order_ids": order_ids}

    def cancel_exit(self, smart_id: str, c: dict | None = None) -> None:
        self._call("POST", f"/order-advance/cancel/FNO/OCO/{smart_id}")
