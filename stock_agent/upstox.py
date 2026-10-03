"""Upstox adapter (Upstox Developer API v2/v3), written from Upstox's published docs.

Login: OAuth. "Log in" opens Upstox's login page; Upstox sends a code back to this app's redirect URL,
which is exchanged for the day's token (valid until 3:30 am the next morning).

Orders: an Upstox GTT must include its entry, so the BUY itself goes out as a GTT bracket, one per exit
leg: ENTRY (limit at the confirmed price) + TARGET + STOPLOSS. Once the entry fills, the target and
stop-loss legs stay live for up to a year. Upstox does not state that one exit leg cancels the other,
so when the app sees one exit fill it cancels the rest of that GTT. Upstox requires a static IP for
API orders; MARKET orders are refused for stock options, so every order is a LIMIT.
"""
from __future__ import annotations

import gzip
import json
import os
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

import requests

from .broker_base import IST, TICK, Broker, BrokerError, tick

API_ROOT = os.environ.get("UPSTOX_API_ROOT", "https://api.upstox.com")
HFT_ROOT = os.environ.get("UPSTOX_HFT_ROOT", "https://api-hft.upstox.com")
INSTRUMENTS_URL = os.environ.get("UPSTOX_INSTRUMENTS_URL",
                                 "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz")
PRODUCT = "D"      # delivery / carry-forward (intraday is "I")


class Upstox(Broker):
    key = "upstox"
    label = "Upstox"
    LOGIN_PARAM = "code"
    EXPIRES_AT = (3, 30)
    BRACKET = True
    FIELDS = [{"name": "api_key", "label": "API key", "help": "Upstox Developer Apps > your app"},
              {"name": "api_secret", "label": "API secret", "secret": True}]
    SETUP = ["Open <a href=\"https://account.upstox.com/developer/apps\" target=\"_blank\" rel=\"noopener\">Upstox "
             "Developer Apps</a> and create an app.",
             "Set its <b>Redirect URL</b> to <b>{redirect}</b>.",
             "Add your <b>static IP</b> in the app settings (Upstox rejects API orders from other IPs).",
             "Copy the <b>API key</b> and <b>API secret</b> below and save.",
             "Each morning press <b>Log in</b>. Upstox access expires at 3:30 am."]

    def login_url(self, redirect: str | None = None) -> str:
        if not self.cfg.get("api_key"):
            raise BrokerError("Add your Upstox API key in Settings first")
        if redirect:
            self.cfg["redirect"] = redirect
            self.save()
        return f"{API_ROOT}/v2/login/authorization/dialog?" + urlencode(
            {"response_type": "code", "client_id": self.cfg["api_key"], "redirect_uri": self.cfg.get("redirect", "")})

    def complete_login(self, code: str) -> dict:
        try:
            r = requests.post(f"{API_ROOT}/v2/login/authorization/token", data={
                "code": code, "client_id": self.cfg.get("api_key"), "client_secret": self.cfg.get("api_secret"),
                "redirect_uri": self.cfg.get("redirect", ""), "grant_type": "authorization_code"},
                headers={"accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}, timeout=20)
            body = r.json()
        except (requests.RequestException, ValueError) as exc:
            raise BrokerError(f"Could not reach Upstox: {exc}") from exc
        if r.status_code >= 400 or not body.get("access_token"):
            raise BrokerError(f"Upstox login failed: {self._err(body) or r.status_code}")
        self._store_token(body["access_token"], body.get("user_name") or body.get("user_id"))
        return self.status()

    # -- HTTP
    @staticmethod
    def _err(body: dict) -> str:
        errs = body.get("errors") or []
        return "; ".join(f"{e.get('message')} ({e.get('error_code')})" for e in errs) or body.get("message") or ""

    def _call(self, method: str, url: str, params=None, body=None):
        self.need_login()
        h = {"Accept": "application/json", "Authorization": f"Bearer {self.cfg['access_token']}"}
        if body is not None:
            h["Content-Type"] = "application/json"
        try:
            r = requests.request(method, url, params=params, json=body, headers=h, timeout=20)
            data = r.json()
        except requests.RequestException as exc:
            raise BrokerError(f"Could not reach Upstox: {exc}") from exc
        except ValueError:
            raise BrokerError(f"Upstox returned HTTP {r.status_code}") from None
        if r.status_code >= 400 or data.get("status") == "error":
            msg = self._err(data) or f"HTTP {r.status_code}"
            if r.status_code == 401:
                self.cfg.pop("access_token", None)
                self.save()
                msg += " (log in to Upstox again)"
            if "UDAPI1154" in msg:
                msg += ": register this computer's static IP in your Upstox app settings"
            raise BrokerError(f"Upstox: {msg}")
        return data.get("data")

    # -- instruments: NSE options and equities from Upstox's daily file
    def instruments(self, cache_dir: Path) -> list[dict]:
        path = cache_dir / "upstox_nse.json"
        if not (path.exists() and date.fromtimestamp(path.stat().st_mtime) == date.today()):
            try:
                raw = requests.get(INSTRUMENTS_URL, timeout=60).content
                rows = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
            except (requests.RequestException, ValueError, OSError) as exc:
                raise BrokerError(f"Could not download Upstox's instrument list: {exc}") from exc
            keep = [r for r in rows if (r.get("segment") == "NSE_FO" and r.get("instrument_type") in ("CE", "PE"))
                    or (r.get("segment") == "NSE_EQ" and r.get("instrument_type") == "EQ")]
            if not keep:
                raise BrokerError("Upstox's instrument list was empty")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(keep), encoding="utf-8")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _expiry(r: dict) -> str:
        e = r.get("expiry")
        if isinstance(e, (int, float)):           # epoch milliseconds (23:59:59 IST on expiry day)
            return datetime.fromtimestamp(e / 1000, IST).date().isoformat()
        return str(e)[:10]

    def find_contract(self, cache_dir: Path, symbol: str, expiry: date, kind: str, strike: float, spot: float) -> dict:
        want = "CE" if kind == "call" else "PE"
        allrows = self.instruments(cache_dir)
        rows = [r for r in allrows if r.get("segment") == "NSE_FO" and r.get("underlying_symbol") == symbol
                and r.get("instrument_type") == want]
        if not rows:
            raise BrokerError(f"{symbol} has no options on NSE F&O")
        same = [r for r in rows if self._expiry(r) == expiry.isoformat()]
        if not same:
            later = sorted({self._expiry(r) for r in rows if self._expiry(r) >= expiry.isoformat()})
            if not later:
                raise BrokerError(f"No {symbol} option expiry on or after {expiry}")
            same = [r for r in rows if self._expiry(r) == later[0]]
        exact = [r for r in same if abs(float(r["strike_price"]) - strike) < 1e-6]
        row = exact[0] if exact else min(same, key=lambda r: abs(float(r["strike_price"]) - spot))
        eq = next((r for r in allrows if r.get("segment") == "NSE_EQ" and r.get("trading_symbol") == symbol), None)
        return {"tradingsymbol": row["trading_symbol"], "instrument_key": row["instrument_key"],
                "lot_size": int(row["lot_size"]), "strike": float(row["strike_price"]), "expiry": self._expiry(row),
                "tick_size": float(row.get("tick_size") or 5) / 100,     # Upstox lists tick size in paise
                "underlying": symbol, "underlying_key": eq["instrument_key"] if eq else row.get("underlying_key")}

    def live(self, c: dict, symbol: str | None) -> dict | None:
        keys = [c["instrument_key"]] + ([c["underlying_key"]] if symbol and c.get("underlying_key") else [])
        try:
            data = self._call("GET", f"{API_ROOT}/v3/market-quote/quotes", params={"instrument_key": ",".join(keys)}) or {}
        except BrokerError as exc:
            if "log in" in str(exc):
                raise
            return None
        by_key = {v.get("instrument_token"): v for v in data.values() if isinstance(v, dict)}
        oq, sq = by_key.get(c["instrument_key"]) or {}, by_key.get(c.get("underlying_key")) or {}
        if not oq:
            return None
        depth = oq.get("depth") or {}
        bid = ((depth.get("buy") or [{}])[0]).get("price") or 0
        ask = ((depth.get("sell") or [{}])[0]).get("price") or 0
        return {"ltp": float(oq.get("last_price") or 0), "bid": float(bid), "ask": float(ask),
                "spot": float(sq.get("last_price") or 0) or None}

    def funds(self) -> float | None:
        try:
            d = self._call("GET", f"{API_ROOT}/v2/user/get-funds-and-margin", params={"segment": "SEC"}) or {}
            return float((d.get("equity") or d).get("available_margin"))
        except Exception:
            return None

    # -- orders
    def place_order(self, c: dict, side: str, qty: int, price: float) -> str:
        d = self._call("POST", f"{HFT_ROOT}/v3/order/place", body={
            "quantity": int(qty), "product": PRODUCT, "validity": "DAY", "price": tick(price, c.get("tick_size") or TICK),
            "tag": "stockagent", "instrument_token": c["instrument_key"], "order_type": "LIMIT", "transaction_type": side,
            "disclosed_quantity": 0, "trigger_price": 0, "is_amo": False, "slice": False})
        return str((d.get("order_ids") or [d.get("order_id")])[0])

    def order_state(self, order_id: str) -> dict:
        d = self._call("GET", f"{API_ROOT}/v2/order/details", params={"order_id": order_id}) or {}
        raw = (d.get("status") or "").lower()
        status = ("COMPLETE" if raw == "complete" else "REJECTED" if raw == "rejected"
                  else "CANCELLED" if raw.startswith("cancelled") else "OPEN")
        return {"status": status, "filled": int(d.get("filled_quantity") or 0),
                "avg_price": float(d.get("average_price") or 0), "message": d.get("status_message")}

    def place_bracket(self, c: dict, qty: int, limit: float, stop: float, target: float) -> str:
        step = c.get("tick_size") or TICK
        d = self._call("POST", f"{API_ROOT}/v3/order/gtt/place", body={
            "type": "MULTIPLE", "quantity": int(qty), "product": PRODUCT, "instrument_token": c["instrument_key"],
            "transaction_type": "BUY", "rules": [
                {"strategy": "ENTRY", "trigger_type": "IMMEDIATE", "trigger_price": tick(limit, step)},
                {"strategy": "TARGET", "trigger_type": "IMMEDIATE", "trigger_price": tick(target, step)},
                {"strategy": "STOPLOSS", "trigger_type": "IMMEDIATE", "trigger_price": tick(stop, step)}]})
        return str((d.get("gtt_order_ids") or [None])[0])

    def bracket_state(self, gid: str, c: dict | None = None) -> dict:
        try:
            d = self._call("GET", f"{API_ROOT}/v3/order/gtt", params={"gtt_order_id": gid})
        except BrokerError as exc:
            if "log in" in str(exc):
                raise
            return {"found": False}
        g = d[0] if isinstance(d, list) and d else d
        if not g:
            return {"found": False}          # Upstox stops listing a GTT once it has completed
        rules = {r.get("strategy"): r for r in g.get("rules") or []}
        entry = rules.get("ENTRY") or {}
        exits = [rules[k] for k in ("TARGET", "STOPLOSS") if k in rules]
        return {"found": True, "entry_order_id": entry.get("order_id"), "entry_status": entry.get("status"),
                "exit_order_ids": [str(r["order_id"]) for r in exits if r.get("order_id")],
                "open_exit_legs": any((r.get("status") or "").upper() in ("SCHEDULED", "PENDING", "OPEN") for r in exits)}

    def cancel_exit(self, gid: str, c: dict | None = None) -> None:
        self._call("DELETE", f"{API_ROOT}/v3/order/gtt/cancel", body={"gtt_order_id": gid})
