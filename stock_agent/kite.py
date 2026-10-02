"""One-click order placement through Zerodha Kite Connect (REST API v3, no extra libraries).

Flow for one trade:
  1. preview(): look up the exact NFO contract, fetch the live quote (if your Kite plan includes market
     data), recompute lots for your risk limit with the live premium, check funds and market hours.
  2. place():   after you confirm, send a LIMIT BUY. Nothing else is sent until that order is filled.
  3. sync():    once filled, set GTT OCO orders on the option (stop-loss + target). With 2+ lots the
                position is split: half exits at Target 1, the rest at Target 2. Later syncs record exits
                that Kite executed so the trade journal closes itself.

Practice mode (the default) runs every step except the three calls that place or cancel orders, and
records what would have been sent. Live mode must be switched on explicitly in Settings.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

API_ROOT = os.environ.get("KITE_API_ROOT", "https://api.kite.trade")
LOGIN_URL = "https://kite.zerodha.com/connect/login?v=3&api_key={key}"
IST = timezone(timedelta(hours=5, minutes=30))
PRODUCT = "NRML"           # carry-forward F&O position (the plan holds for days)
TICK = 0.05


class KiteError(RuntimeError):
    pass


def now_ist() -> datetime:
    return datetime.now(IST)


def market_open(t: datetime | None = None) -> bool:
    t = t or now_ist()
    return t.weekday() < 5 and (t.hour, t.minute) >= (9, 15) and (t.hour, t.minute) < (15, 30)


def tick(x: float, step: float = TICK) -> float:
    return round(round(float(x) / step) * step, 2)


def floor_tick(x: float, step: float = TICK) -> float:
    return round(math.floor(float(x) / step + 1e-9) * step, 2)


# --------------------------------------------------------------------------- config & session
class Kite:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.cfg_path = self.home / "kite.json"
        self.cfg = self._load()

    # -- persistence
    def _load(self) -> dict:
        try:
            return json.loads(self.cfg_path.read_text(encoding="utf-8"))
        except Exception:
            return {"api_key": "", "api_secret": "", "practice": True}

    def save(self) -> None:
        self.cfg_path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg_path.write_text(json.dumps(self.cfg, indent=1), encoding="utf-8")
        try:
            os.chmod(self.cfg_path, 0o600)
        except OSError:
            pass

    def update_config(self, api_key=None, api_secret=None, practice=None) -> dict:
        if api_key is not None and api_key.strip() != self.cfg.get("api_key"):
            self.cfg["api_key"] = api_key.strip()
            self.cfg.pop("access_token", None)
        if api_secret:  # blank means "keep the stored one"
            self.cfg["api_secret"] = api_secret.strip()
        if practice is not None:
            self.cfg["practice"] = bool(practice)
        self.save()
        return self.status()

    # -- session
    def token_valid(self) -> bool:
        tok, at = self.cfg.get("access_token"), self.cfg.get("token_time")
        if not tok or not at:
            return False
        issued = datetime.fromisoformat(at)
        expiry = issued.replace(hour=6, minute=0, second=0, microsecond=0)
        if issued >= expiry:
            expiry += timedelta(days=1)
        return now_ist() < expiry  # Kite tokens expire at 6 am IST

    def status(self) -> dict:
        return {
            "configured": bool(self.cfg.get("api_key") and self.cfg.get("api_secret")),
            "api_key": self.cfg.get("api_key", ""),
            "has_secret": bool(self.cfg.get("api_secret")),
            "connected": self.token_valid(),
            "user": self.cfg.get("user_name") if self.token_valid() else None,
            "practice": self.cfg.get("practice", True),
            "market_open": market_open(),
        }

    def login_url(self) -> str:
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
        self.cfg.update(access_token=data["access_token"], user_name=data.get("user_name") or data.get("user_id"),
                        token_time=now_ist().isoformat())
        self.save()
        return self.status()

    def logout(self) -> dict:
        self.cfg.pop("access_token", None)
        self.save()
        return self.status()

    # -- HTTP
    def _headers(self, auth=True) -> dict:
        h = {"X-Kite-Version": "3", "User-Agent": "stock-agent"}
        if auth:
            if not self.token_valid():
                raise KiteError("Not logged in to Kite today. Press 'Log in to Kite' in Settings.")
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
        return {"tradingsymbol": row["tradingsymbol"], "lot_size": int(float(row["lot_size"])),
                "strike": float(row["strike"]), "expiry": row["expiry"], "tick_size": float(row.get("tick_size") or TICK)}

    def quote(self, keys: list[str]) -> dict | None:
        """Full quotes, or None when the Kite plan has no market-data access."""
        try:
            return self._call("GET", "/quote", params=[("i", k) for k in keys])
        except KiteError as exc:
            if "permission" in str(exc).lower() or "subscription" in str(exc).lower():
                return None
            raise

    def funds(self) -> float | None:
        try:
            eq = self._call("GET", "/user/margins/equity")
            return float(eq["available"].get("live_balance", eq.get("net", 0)))
        except Exception:
            return None

    # -- orders
    def place_order(self, tradingsymbol: str, side: str, qty: int, price: float, tag: str = "stockagent") -> str:
        data = self._call("POST", "/orders/regular", data={
            "exchange": "NFO", "tradingsymbol": tradingsymbol, "transaction_type": side, "quantity": int(qty),
            "product": PRODUCT, "order_type": "LIMIT", "price": f"{price:.2f}", "validity": "DAY", "tag": tag[:20]})
        return str(data["order_id"])

    def order_state(self, order_id: str) -> dict:
        hist = self._call("GET", f"/orders/{order_id}")
        last = hist[-1] if isinstance(hist, list) and hist else (hist or {})
        return {"status": last.get("status"), "filled": int(last.get("filled_quantity") or 0),
                "avg_price": float(last.get("average_price") or 0), "message": last.get("status_message")}

    def place_gtt(self, tradingsymbol: str, qty: int, ltp: float, stop: float, target: float) -> str:
        cond = {"exchange": "NFO", "tradingsymbol": tradingsymbol, "trigger_values": [stop, target], "last_price": ltp}
        legs = [
            # lower trigger = stop-loss: sell a little below the trigger so the order fills in a fast fall
            {"exchange": "NFO", "tradingsymbol": tradingsymbol, "transaction_type": "SELL", "quantity": int(qty),
             "order_type": "LIMIT", "product": PRODUCT, "price": floor_tick(stop * 0.97)},
            {"exchange": "NFO", "tradingsymbol": tradingsymbol, "transaction_type": "SELL", "quantity": int(qty),
             "order_type": "LIMIT", "product": PRODUCT, "price": tick(target)},
        ]
        data = self._call("POST", "/gtt/triggers", data={"type": "two-leg", "condition": json.dumps(cond),
                                                          "orders": json.dumps(legs)})
        return str(data["trigger_id"])

    def gtt_state(self, trigger_id: str) -> dict:
        g = self._call("GET", f"/gtt/triggers/{trigger_id}")
        order_ids = []
        for leg in g.get("orders") or []:
            res = (leg.get("result") or {}).get("order_result") or {}
            if res.get("order_id"):
                order_ids.append(str(res["order_id"]))
        return {"status": g.get("status"), "order_ids": order_ids}

    def cancel_gtt(self, trigger_id: str) -> None:
        self._call("DELETE", f"/gtt/triggers/{trigger_id}")


# --------------------------------------------------------------------------- trade logic
def _reprice(live_premium: float, spot: float, strike: float, expiry: date, kind: str, plan: dict) -> dict:
    """Option premiums at the plan's stock levels, using the volatility implied by the live premium."""
    from .options import bs_price, implied_vol

    t = max((expiry - now_ist().date()).days, 1) / 365
    iv = implied_vol(live_premium, spot, strike, t, kind)
    if not iv:  # fall back to scaling the plan's own estimates
        f = live_premium / plan["premium"] if plan.get("premium") else 1.0
        return {"stop": tick(plan["stop_premium"] * f), "t1": tick(plan["target1_premium"] * f),
                "t2": tick(plan["target2_premium"] * f), "iv": None}
    t1 = max((expiry - now_ist().date()).days - 1, 1) / 365
    r = 0.065
    return {"stop": tick(bs_price(plan["stop_underlying"], strike, t1, iv, kind, r)),
            "t1": tick(bs_price(plan["target1_underlying"], strike, t1, iv, kind, r)),
            "t2": tick(bs_price(plan["target2_underlying"], strike, t1, iv, kind, r)), "iv": iv}


def preview(kite: Kite, cache_dir: Path, ticker: str, plan: dict, capital: float, risk_pct: float,
            limit_override: float | None = None) -> dict:
    """Everything the confirm dialog shows. Raises KiteError when the trade cannot be placed."""
    symbol = ticker.upper().split(".")[0]
    if not ticker.upper().endswith((".NS", ".BO")):
        raise KiteError("Kite places NSE orders only; this pick is not an Indian stock")
    kind = "call" if plan["side"] == "CALL" else "put"
    expiry = date.fromisoformat(plan["expiry"])
    spot = float(plan["entry"])
    c = kite.find_contract(cache_dir, symbol, expiry, kind, float(plan["strike"]), spot)
    exp = date.fromisoformat(c["expiry"])
    q = kite.quote([f"NFO:{c['tradingsymbol']}", f"NSE:{symbol}"])
    live = None
    warnings = []
    if q:
        oq = q.get(f"NFO:{c['tradingsymbol']}") or {}
        sq = q.get(f"NSE:{symbol}") or {}
        spot = float(sq.get("last_price") or spot)
        ask = ((oq.get("depth") or {}).get("sell") or [{}])[0].get("price") or 0
        bid = ((oq.get("depth") or {}).get("buy") or [{}])[0].get("price") or 0
        live = {"ltp": float(oq.get("last_price") or 0), "bid": float(bid), "ask": float(ask), "spot": spot}
        if live["ask"] and live["bid"] and (live["ask"] - live["bid"]) / live["ask"] > 0.05:
            warnings.append(f"Wide bid/ask spread ({live['bid']:.2f} / {live['ask']:.2f}): you lose money just entering")
    else:
        warnings.append("Your Kite plan has no live quotes, so check the premium in Kite and type your limit price")
    limit = limit_override or (live and (live["ask"] or live["ltp"])) or plan["premium"]
    limit = tick(limit)
    levels = _reprice(limit, spot, c["strike"], exp, kind, plan)
    lot = c["lot_size"]
    per_lot_risk = max(limit - levels["stop"], TICK) * lot
    budget = capital * risk_pct
    lots = int(budget // per_lot_risk)
    lots = min(lots, int((capital * 0.25) // (limit * lot)))
    funds = kite.funds()
    cost = limit * lot * lots
    blockers = []
    if lots <= 0:
        blockers.append(f"1 lot risks ₹{per_lot_risk:,.0f} to the stop, above your ₹{budget:,.0f} limit")
    if funds is not None and cost > funds:
        blockers.append(f"Not enough funds: needs ₹{cost:,.0f}, available ₹{funds:,.0f}")
    if not kite.cfg.get("practice", True) and not market_open():
        blockers.append("Market is closed. Live orders can be placed between 9:15 am and 3:30 pm IST")
    if not (levels["stop"] < limit < levels["t1"]):
        blockers.append("Live premium has moved past the plan's levels; re-scan before trading")
    legs = []
    if lots >= 2:
        a = math.ceil(lots / 2)
        legs = [{"lots": a, "qty": a * lot, "stop": levels["stop"], "target": levels["t1"], "label": "Target 1"},
                {"lots": lots - a, "qty": (lots - a) * lot, "stop": levels["stop"], "target": levels["t2"], "label": "Target 2"}]
    elif lots == 1:
        legs = [{"lots": 1, "qty": lot, "stop": levels["stop"], "target": levels["t1"], "label": "Target 1"}]
        warnings.append("With 1 lot the position cannot be split, so all of it exits at Target 1")
    if (exp - now_ist().date()).days < 7:
        warnings.append("Expiry is less than a week away: time decay is fast")
    return {
        "ticker": ticker, "tradingsymbol": c["tradingsymbol"], "expiry": c["expiry"], "strike": c["strike"],
        "lot_size": lot, "lots": max(lots, 0), "qty": max(lots, 0) * lot, "limit": limit, "cost": cost,
        "max_loss_at_stop": per_lot_risk * max(lots, 0), "budget": budget, "funds": funds, "live": live,
        "levels": levels, "gtt_legs": legs, "side": plan["side"], "practice": kite.cfg.get("practice", True),
        "market_open": market_open(), "warnings": warnings, "blockers": blockers,
        "stock_stop": plan["stop_underlying"], "time_exit": plan.get("time_stop"),
    }


def place(kite: Kite, pv: dict, wait_seconds: float = 20) -> dict:
    """Send the BUY (or simulate it in practice mode), wait briefly for a fill, then set the GTTs."""
    if pv["blockers"]:
        raise KiteError("; ".join(pv["blockers"]))
    rec = {"broker": "kite", "practice": pv["practice"], "tradingsymbol": pv["tradingsymbol"], "quantity": pv["qty"],
           "buy_price": pv["limit"], "gtt_legs": pv["gtt_legs"], "gtt_ids": [], "exits": [], "events": []}
    if pv["practice"]:
        rec.update(buy_order_id="PRACTICE", buy_status="COMPLETE", filled_qty=pv["qty"], avg_price=pv["limit"],
                   gtt_ids=[f"PRACTICE-{i + 1}" for i in range(len(pv["gtt_legs"]))])
        rec["events"].append("Practice: BUY order and stop/target GTTs simulated, nothing sent to Kite")
        return rec
    oid = kite.place_order(pv["tradingsymbol"], "BUY", pv["qty"], pv["limit"])
    rec.update(buy_order_id=oid, buy_status="OPEN", filled_qty=0, avg_price=None)
    rec["events"].append(f"BUY {pv['qty']} {pv['tradingsymbol']} @ {pv['limit']:.2f} sent (order {oid})")
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        st = kite.order_state(oid)
        rec["buy_status"] = st["status"]
        if st["status"] in ("COMPLETE", "REJECTED", "CANCELLED"):
            break
        time.sleep(1.5)
    return sync_one(kite, rec)


def sync_one(kite: Kite, rec: dict) -> dict:
    """Advance one live trade: record the fill, set GTTs once filled, record executed exits."""
    if rec.get("practice") or rec.get("broker") != "kite":
        return rec
    if not rec.get("gtt_ids"):
        st = kite.order_state(rec["buy_order_id"])
        rec["buy_status"] = st["status"]
        if st["status"] in ("REJECTED", "CANCELLED"):
            rec["events"].append(f"BUY {st['status'].lower()}: {st.get('message') or 'no reason given'}")
            return rec
        if st["status"] != "COMPLETE":
            return rec  # still waiting; nothing protective is placed on an unfilled order
        rec["filled_qty"], rec["avg_price"] = st["filled"], st["avg_price"]
        rec["events"].append(f"BUY filled: {st['filled']} @ {st['avg_price']:.2f}")
        q = kite.quote([f"NFO:{rec['tradingsymbol']}"]) or {}
        ltp = float((q.get(f"NFO:{rec['tradingsymbol']}") or {}).get("last_price") or st["avg_price"])
        lot = rec["quantity"] // max(sum(l["lots"] for l in rec["gtt_legs"]), 1)
        remaining = st["filled"]
        for leg in rec["gtt_legs"]:
            qty = min(leg["lots"] * lot, remaining)
            if qty <= 0:
                break
            remaining -= qty
            if not (leg["stop"] < ltp < leg["target"]):
                rec["events"].append(f"Premium {ltp:.2f} is already outside {leg['stop']:.2f}-{leg['target']:.2f}; "
                                     f"set this exit by hand in Kite")
                continue
            gid = kite.place_gtt(rec["tradingsymbol"], qty, ltp, leg["stop"], leg["target"])
            rec["gtt_ids"].append(gid)
            rec["events"].append(f"GTT {gid}: sell {qty} at stop {leg['stop']:.2f} or {leg['label']} {leg['target']:.2f}")
        return rec
    # GTTs live: look for executed exit orders
    seen = {e["order_id"] for e in rec["exits"]}
    for gid in rec["gtt_ids"]:
        g = kite.gtt_state(gid)
        for oid in g["order_ids"]:
            if oid in seen:
                continue
            st = kite.order_state(oid)
            if st["status"] == "COMPLETE":
                rec["exits"].append({"order_id": oid, "qty": st["filled"], "price": st["avg_price"], "via": f"GTT {gid}"})
                rec["events"].append(f"Exit filled via GTT {gid}: {st['filled']} @ {st['avg_price']:.2f}")
    for e in rec.get("manual_exit_ids", []):
        if e in seen:
            continue
        st = kite.order_state(e)
        if st["status"] == "COMPLETE":
            rec["exits"].append({"order_id": e, "qty": st["filled"], "price": st["avg_price"], "via": "Exit now"})
            rec["events"].append(f"Exit filled: {st['filled']} @ {st['avg_price']:.2f}")
    return rec


def exit_now(kite: Kite, rec: dict, price: float | None = None) -> dict:
    """Cancel the GTTs and sell what is still held at the best bid (or the price given)."""
    held = int(rec.get("filled_qty") or 0) - sum(int(e["qty"]) for e in rec.get("exits", []))
    if held <= 0:
        raise KiteError("Nothing left to sell")
    if rec.get("practice"):
        px = price or rec.get("avg_price") or rec.get("buy_price")
        rec["exits"].append({"order_id": "PRACTICE", "qty": held, "price": float(px), "via": "Exit now (practice)"})
        rec["events"].append(f"Practice exit: {held} @ {float(px):.2f}")
        return rec
    if not market_open():
        raise KiteError("Market is closed. Exit between 9:15 am and 3:30 pm IST")
    if price is None:
        q = kite.quote([f"NFO:{rec['tradingsymbol']}"])
        if not q:
            raise KiteError("No live quote on your Kite plan: type the price to sell at")
        oq = q.get(f"NFO:{rec['tradingsymbol']}") or {}
        price = ((oq.get("depth") or {}).get("buy") or [{}])[0].get("price") or oq.get("last_price")
    for gid in rec.get("gtt_ids", []):
        try:
            kite.cancel_gtt(gid)
            rec["events"].append(f"GTT {gid} cancelled")
        except KiteError as exc:
            rec["events"].append(f"GTT {gid} not cancelled: {exc}")
    oid = kite.place_order(rec["tradingsymbol"], "SELL", held, tick(price))
    rec.setdefault("manual_exit_ids", []).append(oid)
    rec["events"].append(f"SELL {held} @ {tick(price):.2f} sent (order {oid})")
    return rec
