"""Broker-neutral order flow for one trade, shared by Zerodha Kite, Upstox, Groww and 5paisa.

Flow:
  1. preview(): look up the exact NSE option contract on the broker, fetch the live quote (when the account
     has market data), recompute lots for the risk limit with the live premium, check funds and hours.
  2. place():   after the person confirms, send a LIMIT BUY. Nothing else is sent until it is filled.
  3. sync_one(): once filled, protect the position with the broker's stop-loss + target order (GTT/OCO where
     the broker has one); later syncs record exits so the trade journal closes itself.

Practice mode (the default for every broker) runs every step except the calls that place or cancel orders,
and records what would have been sent. Live mode must be switched on explicitly in Settings.

Each broker adapter provides:
  key, label, FIELDS (settings it needs), EXPIRES_AT (hour, minute IST its daily login expires)
  login_url(redirect) / complete_login(params) / _headers / _call
  find_contract(cache_dir, symbol, expiry, kind, strike, spot) -> dict with "tradingsymbol", "lot_size",
      "strike", "expiry", "tick_size" and whatever ids the broker needs
  live(contract, symbol) -> {"ltp", "bid", "ask", "spot"} or None when the account has no market data
  funds() -> float | None
  place_order(contract, side, qty, price) -> order id
  order_state(order_id) -> {"status": COMPLETE/REJECTED/CANCELLED/OPEN, "filled", "avg_price", "message"}
  place_exit(contract, qty, ltp, stop, target) -> id;  exit_state(id, contract) -> {"status", "order_ids"};
  cancel_exit(id, contract)
  LOGIN_KIND: "redirect" (login_url + complete_login from the broker's redirect) or "direct" (direct_login())
Brokers whose stop/target order must include the entry (Upstox GTT) set BRACKET = True and provide
  place_bracket(contract, qty, limit, stop, target) -> id and bracket_state(id, contract) ->
  {"entry_order_id", "entry_status", "exit_order_ids", "open_exit_legs", "found"}; the BUY then goes out as
  one bracket per exit leg instead of a separate order.
An exit order that the broker reports EXPIRED (some brokers' stop/target orders last one day) while the
position is still held is placed again on the next sync.
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
TICK = 0.05


class BrokerError(RuntimeError):
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


class Broker:
    """Settings, daily login and practice mode common to every broker."""

    key = "broker"
    label = "Broker"
    FIELDS: list[dict] = []          # [{"name", "label", "secret": bool, "help"}]
    EXPIRES_AT = (6, 0)              # access tokens die at this time IST the next morning
    LOGIN_PARAM = "request_token"    # query parameter the broker's login redirect carries
    LOGIN_KIND = "redirect"
    BRACKET = False
    EXIT_NOTE: str | None = None     # shown in the order preview when exits need attention
    SETUP: list[str] = []            # one-time setup steps shown in Settings (HTML allowed)

    def __init__(self, home: Path):
        self.home = Path(home)
        self.cfg_path = self.home / f"{self.key}.json"
        self.cfg = self._load()

    # -- persistence
    def _load(self) -> dict:
        try:
            return json.loads(self.cfg_path.read_text(encoding="utf-8"))
        except Exception:
            return {"practice": True}

    def save(self) -> None:
        self.cfg_path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg_path.write_text(json.dumps(self.cfg, indent=1), encoding="utf-8")
        try:
            os.chmod(self.cfg_path, 0o600)
        except OSError:
            pass

    def update(self, values: dict, practice=None) -> dict:
        """Store settings; blank secret fields keep the stored value. A new key logs the session out."""
        for f in self.FIELDS:
            v = values.get(f["name"])
            if v is None or (f.get("secret") and v == ""):
                continue
            v = str(v).strip()
            if v != self.cfg.get(f["name"]):
                self.cfg[f["name"]] = v
                self.cfg.pop("access_token", None)
        if practice is not None:
            self.cfg["practice"] = bool(practice)
        self.save()
        return self.status()

    def configured(self) -> bool:
        return all(self.cfg.get(f["name"]) for f in self.FIELDS if not f.get("optional"))

    # -- session
    def token_valid(self) -> bool:
        tok, at = self.cfg.get("access_token"), self.cfg.get("token_time")
        if not tok or not at:
            return False
        issued = datetime.fromisoformat(at)
        h, m = self.EXPIRES_AT
        expiry = issued.replace(hour=h, minute=m, second=0, microsecond=0)
        if issued >= expiry:
            expiry += timedelta(days=1)
        return now_ist() < expiry

    def _store_token(self, token: str, user: str | None) -> None:
        self.cfg.update(access_token=token, user_name=user, token_time=now_ist().isoformat())
        self.save()

    def status(self) -> dict:
        return {
            "broker": self.key, "label": self.label, "configured": self.configured(),
            "fields": [{**f, "value": "" if f.get("secret") else self.cfg.get(f["name"], ""),
                        "has_value": bool(self.cfg.get(f["name"]))} for f in self.FIELDS],
            "api_key": self.cfg.get("api_key", ""), "has_secret": bool(self.cfg.get("api_secret")),
            "connected": self.token_valid(), "user": self.cfg.get("user_name") if self.token_valid() else None,
            "practice": self.cfg.get("practice", True), "market_open": market_open(),
            "login_kind": self.LOGIN_KIND, "setup": self.SETUP,
        }

    def logout(self) -> dict:
        self.cfg.pop("access_token", None)
        self.save()
        return self.status()

    def need_login(self) -> None:
        if not self.token_valid():
            raise BrokerError(f"Not logged in to {self.label} today. Press 'Log in' in Settings.")


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


def preview(broker: Broker, cache_dir: Path, ticker: str, plan: dict, capital: float, risk_pct: float,
            limit_override: float | None = None) -> dict:
    """Everything the confirm dialog shows. Raises BrokerError when the trade cannot be placed."""
    name = broker.label
    symbol = ticker.upper().split(".")[0]
    if not ticker.upper().endswith((".NS", ".BO")):
        raise BrokerError(f"{name} places NSE orders only; this pick is not an Indian stock")
    kind = "call" if plan["side"] == "CALL" else "put"
    expiry = date.fromisoformat(plan["expiry"])
    spot = float(plan["entry"])
    c = broker.find_contract(cache_dir, symbol, expiry, kind, float(plan["strike"]), spot)
    exp = date.fromisoformat(c["expiry"])
    live = broker.live(c, symbol)
    warnings = []
    if live:
        spot = float(live.get("spot") or spot)
        if live["ask"] and live["bid"] and (live["ask"] - live["bid"]) / live["ask"] > 0.05:
            warnings.append(f"Wide bid/ask spread ({live['bid']:.2f} / {live['ask']:.2f}): you lose money just entering")
    else:
        warnings.append(f"No live quotes from {name}, so check the premium in the {name} app and type your limit price")
    step = float(c.get("tick_size") or TICK)
    limit = tick(limit_override or (live and (live["ask"] or live["ltp"])) or plan["premium"], step)
    levels = _reprice(limit, spot, c["strike"], exp, kind, plan)
    lot = c["lot_size"]
    per_lot_risk = max(limit - levels["stop"], step) * lot
    budget = capital * risk_pct
    lots = int(budget // per_lot_risk)
    lots = min(lots, int((capital * 0.25) // (limit * lot)))
    funds = broker.funds()
    cost = limit * lot * lots
    blockers = []
    if lots <= 0:
        blockers.append(f"1 lot risks ₹{per_lot_risk:,.0f} to the stop, above your ₹{budget:,.0f} limit")
    if funds is not None and cost > funds:
        blockers.append(f"Not enough funds: needs ₹{cost:,.0f}, available ₹{funds:,.0f}")
    if not broker.cfg.get("practice", True) and not market_open():
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
    if getattr(broker, "EXIT_NOTE", None):
        warnings.append(broker.EXIT_NOTE)
    if (exp - now_ist().date()).days < 7:
        warnings.append("Expiry is less than a week away: time decay is fast")
    from .tradetest import load_validation

    v = load_validation()
    if v and (v.get("test_avg_ret") or 0) <= 0:
        warnings.insert(0, f"This strategy lost money on recent untouched data ({v['test_win_rate']:.0%} of "
                           f"{v['test_trades']:,} trades won, average {v['test_avg_ret']:+.1%}). There is no proven edge.")
    from . import checklist as C

    spread = ((live["ask"] - live["bid"]) / live["ask"]) if live and live["ask"] and live["bid"] else None
    rd = C.next_results_date(ticker, cache_dir)
    texit = date.fromisoformat(plan["time_stop"]) if plan.get("time_stop") else None
    edge_detail = (f"strategy check {v['test_avg_ret']:+.1%} per trade on unseen data" if v else "no strategy check yet")
    cl = C.evaluate(edge_ok=bool(v) and (v.get("test_avg_ret") or -1) > 0 and plan.get("setup_edge_ok", True),
                    edge_detail=edge_detail, stop=plan["stop_underlying"], target=plan["target1_underlying"],
                    time_exit=plan.get("time_stop"), max_loss=per_lot_risk * max(lots, 0) if lots > 0 else None,
                    budget=budget, affordable=lots > 0, spread_pct=spread, results_date=rd, results_known=True,
                    exit_date=texit, intraday=False)
    return {
        "checklist": cl, "broker": broker.key, "broker_label": name, "contract": c,
        "ticker": ticker, "tradingsymbol": c["tradingsymbol"], "expiry": c["expiry"], "strike": c["strike"],
        "lot_size": lot, "lots": max(lots, 0), "qty": max(lots, 0) * lot, "limit": limit, "cost": cost,
        "max_loss_at_stop": per_lot_risk * max(lots, 0), "budget": budget, "funds": funds, "live": live,
        "levels": levels, "gtt_legs": legs, "side": plan["side"], "practice": broker.cfg.get("practice", True),
        "market_open": market_open(), "warnings": warnings, "blockers": blockers,
        "stock_stop": plan["stop_underlying"], "time_exit": plan.get("time_stop"),
    }


def place(broker: Broker, pv: dict, wait_seconds: float = 20, confirmed: list | None = None) -> dict:
    """Send the BUY (or simulate it in practice mode), wait briefly for a fill, then set the exits.

    Live orders also need the six-point checklist: every automatic item must pass and every item only
    the trader can answer must be in `confirmed`. Practice orders are allowed either way.
    """
    if pv["blockers"]:
        raise BrokerError("; ".join(pv["blockers"]))
    cl = pv.get("checklist")
    if cl and not pv["practice"]:
        failed = [i["title"] for i in cl["items"] if i["status"] == "fail"]
        unticked = [i["title"] for i in cl["items"] if i["status"] == "check" and i["id"] not in (confirmed or [])]
        if failed:
            raise BrokerError("Checklist failed (" + ", ".join(failed) + "): take a trade only if every answer is yes")
        if unticked:
            raise BrokerError("Tick the checklist items you have checked yourself: " + ", ".join(unticked))
    c = pv.get("contract") or {"tradingsymbol": pv["tradingsymbol"]}
    rec = {"broker": broker.key, "practice": pv["practice"], "tradingsymbol": pv["tradingsymbol"], "contract": c,
           "quantity": pv["qty"], "buy_price": pv["limit"], "gtt_legs": pv["gtt_legs"], "gtt_ids": [], "exits": [],
           "events": [],
           # for the order record: what the screen showed when you pressed Place, to compare with the fill
           "planned": {"quote": (pv.get("live") or {}).get("ask") or (pv.get("live") or {}).get("ltp"), "limit": pv["limit"],
                       "at": now_ist().isoformat(timespec="seconds")}}
    if pv["practice"]:
        rec.update(buy_order_id="PRACTICE", buy_status="COMPLETE", filled_qty=pv["qty"], avg_price=pv["limit"],
                   gtt_ids=[f"PRACTICE-{i + 1}" for i in range(len(pv["gtt_legs"]))])
        rec["events"].append(f"Practice: BUY order and stop/target exits simulated, nothing sent to {broker.label}")
        return rec
    if getattr(broker, "BRACKET", False):
        lot = pv["lot_size"]
        rec.update(buy_order_id=None, buy_status="OPEN", filled_qty=0, avg_price=None, bracket=True)
        for leg in pv["gtt_legs"]:
            gid = broker.place_bracket(c, leg["lots"] * lot, pv["limit"], leg["stop"], leg["target"])
            rec["gtt_ids"].append(gid)
            rec.setdefault("exit_legs", []).append({"id": gid, "qty": leg["lots"] * lot, "stop": leg["stop"],
                                                    "target": leg["target"], "label": leg["label"]})
            rec["events"].append(f"{broker.label} GTT {gid}: BUY {leg['lots'] * lot} @ {pv['limit']:.2f}, then sell at stop "
                                 f"{leg['stop']:.2f} or {leg['label']} {leg['target']:.2f}")
        return sync_one(broker, rec)
    oid = broker.place_order(c, "BUY", pv["qty"], pv["limit"])
    rec.update(buy_order_id=oid, buy_status="OPEN", filled_qty=0, avg_price=None)
    rec["events"].append(f"BUY {pv['qty']} {pv['tradingsymbol']} @ {pv['limit']:.2f} sent to {broker.label} (order {oid})")
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        st = broker.order_state(oid)
        rec["buy_status"] = st["status"]
        if st["status"] in ("COMPLETE", "REJECTED", "CANCELLED"):
            break
        time.sleep(1.5)
    return sync_one(broker, rec)


def sync_one(broker: Broker, rec: dict) -> dict:
    """Advance one live trade: record the fill, set exits once filled, record executed exits."""
    if rec.get("practice") or rec.get("broker", "kite") != broker.key:
        return rec
    c = rec.get("contract") or {"tradingsymbol": rec["tradingsymbol"]}
    if rec.get("bracket"):
        return _sync_bracket(broker, rec, c)
    if not rec.get("gtt_ids"):
        st = broker.order_state(rec["buy_order_id"])
        rec["buy_status"] = st["status"]
        if st["status"] in ("REJECTED", "CANCELLED"):
            rec["events"].append(f"BUY {st['status'].lower()}: {st.get('message') or 'no reason given'}")
            return rec
        if st["status"] != "COMPLETE":
            return rec  # still waiting; nothing protective is placed on an unfilled order
        rec["filled_qty"], rec["avg_price"] = st["filled"], st["avg_price"]
        rec["events"].append(f"BUY filled: {st['filled']} @ {st['avg_price']:.2f}")
        live = broker.live(c, None) or {}
        ltp = float(live.get("ltp") or st["avg_price"])
        lot = rec["quantity"] // max(sum(l["lots"] for l in rec["gtt_legs"]), 1)
        remaining = st["filled"]
        for leg in rec["gtt_legs"]:
            qty = min(leg["lots"] * lot, remaining)
            if qty <= 0:
                break
            remaining -= qty
            if not (leg["stop"] < ltp < leg["target"]):
                rec["events"].append(f"Premium {ltp:.2f} is already outside {leg['stop']:.2f}-{leg['target']:.2f}; "
                                     f"set this exit by hand in {broker.label}")
                continue
            gid = broker.place_exit(c, qty, ltp, leg["stop"], leg["target"])
            rec["gtt_ids"].append(gid)
            rec.setdefault("exit_legs", []).append({"id": gid, "qty": qty, "stop": leg["stop"], "target": leg["target"],
                                                    "label": leg["label"]})
            rec["events"].append(f"Exit order {gid}: sell {qty} at stop {leg['stop']:.2f} or {leg['label']} {leg['target']:.2f}")
        return rec
    seen = {e["order_id"] for e in rec["exits"]} | set(rec.get("manual_exit_ids", []))
    for gid in list(rec["gtt_ids"]):
        g = broker.exit_state(gid, c)
        if (g.get("status") or "").upper() == "EXPIRED" and not g["order_ids"]:
            leg = next((l for l in rec.get("exit_legs", []) if l["id"] == gid), None)
            held = int(rec.get("filled_qty") or 0) - sum(int(e["qty"]) for e in rec["exits"])
            today = now_ist().date().isoformat()
            if leg and held > 0 and market_open() and leg.get("replaced_on") != today:
                leg["replaced_on"] = today
                live = broker.live(c, None) or {}
                ltp = float(live.get("ltp") or 0)
                if ltp and leg["stop"] < ltp < leg["target"]:
                    new = broker.place_exit(c, min(leg["qty"], held), ltp, leg["stop"], leg["target"])
                    rec["gtt_ids"][rec["gtt_ids"].index(gid)] = new
                    leg["id"] = new
                    rec["events"].append(f"Exit order {gid} expired; placed again as {new}")
                else:
                    rec["events"].append(f"Exit order {gid} expired and the premium {ltp:.2f} is outside the stop/target: "
                                         f"exit by hand in {broker.label}")
            continue
        for oid in g["order_ids"]:
            if oid in seen:
                continue
            st = broker.order_state(oid)
            if st["status"] == "COMPLETE":
                rec["exits"].append({"order_id": oid, "qty": st["filled"], "price": st["avg_price"], "via": f"exit {gid}"})
                rec["events"].append(f"Exit filled via {gid}: {st['filled']} @ {st['avg_price']:.2f}")
                seen.add(oid)
    recorded = {e["order_id"] for e in rec["exits"]}
    for e in rec.get("manual_exit_ids", []):
        if e in recorded:
            continue
        st = broker.order_state(e)
        if st["status"] == "COMPLETE":
            rec["exits"].append({"order_id": e, "qty": st["filled"], "price": st["avg_price"], "via": "Exit now"})
            rec["events"].append(f"Exit filled: {st['filled']} @ {st['avg_price']:.2f}")
    return rec


def _sync_bracket(broker: Broker, rec: dict, c: dict) -> dict:
    """Entry fills and exit fills of bracket orders (entry + target + stop in one broker order)."""
    seen = {e["order_id"] for e in rec["exits"]} | set(rec.get("manual_exit_ids", []))
    fills, done, rejected = [], 0, 0
    for gid in rec["gtt_ids"]:
        b = broker.bracket_state(gid, c)
        if not b.get("found", True):
            if f"gone-{gid}" not in rec.get("noted", []):
                rec.setdefault("noted", []).append(f"gone-{gid}")
                rec["events"].append(f"{broker.label} no longer lists GTT {gid} (it has finished). Check the fills in "
                                     f"{broker.label} and close the trade here if it has sold.")
            continue
        eid = b.get("entry_order_id")
        if eid:
            st = broker.order_state(eid)
            if st["status"] == "COMPLETE":
                fills.append((st["filled"], st["avg_price"]))
                done += 1
            elif st["status"] in ("REJECTED", "CANCELLED"):
                rejected += 1
                if f"entry-{gid}" not in rec.get("noted", []):
                    rec.setdefault("noted", []).append(f"entry-{gid}")
                    rec["events"].append(f"BUY in {gid} {st['status'].lower()}: {st.get('message') or 'no reason given'}")
        elif (b.get("entry_status") or "").upper() in ("FAILED", "CANCELLED", "EXPIRED"):
            rejected += 1
        for oid in b.get("exit_order_ids", []):
            if oid in seen:
                continue
            st = broker.order_state(oid)
            if st["status"] == "COMPLETE":
                rec["exits"].append({"order_id": oid, "qty": st["filled"], "price": st["avg_price"], "via": f"GTT {gid}"})
                rec["events"].append(f"Exit filled via {gid}: {st['filled']} @ {st['avg_price']:.2f}")
                seen.add(oid)
                if b.get("open_exit_legs"):     # safety: never leave the other exit leg live once one has sold
                    try:
                        broker.cancel_exit(gid, c)
                        rec["events"].append(f"Remaining exit leg of {gid} cancelled")
                    except BrokerError as exc:
                        rec["events"].append(f"Could not cancel the other exit leg of {gid}: {exc}. Check {broker.label}.")
    if fills:
        qty = sum(q for q, _ in fills)
        rec["filled_qty"], rec["avg_price"] = qty, sum(q * p for q, p in fills) / qty if qty else None
    if done == len(rec["gtt_ids"]):
        if rec.get("buy_status") != "COMPLETE":
            rec["events"].append(f"BUY filled: {rec['filled_qty']} @ {rec['avg_price']:.2f}")
        rec["buy_status"] = "COMPLETE"
    elif rejected == len(rec["gtt_ids"]):
        rec["buy_status"] = "REJECTED"
    elif done:
        rec["buy_status"] = "COMPLETE"   # part of the position is held and protected
    return rec


def exit_now(broker: Broker, rec: dict, price: float | None = None) -> dict:
    """Cancel the exit orders and sell what is still held at the best bid (or the price given)."""
    held = int(rec.get("filled_qty") or 0) - sum(int(e["qty"]) for e in rec.get("exits", []))
    if held <= 0:
        raise BrokerError("Nothing left to sell")
    if rec.get("practice"):
        px = price or rec.get("avg_price") or rec.get("buy_price")
        rec["exits"].append({"order_id": "PRACTICE", "qty": held, "price": float(px), "via": "Exit now (practice)"})
        rec["events"].append(f"Practice exit: {held} @ {float(px):.2f}")
        return rec
    if not market_open():
        raise BrokerError("Market is closed. Exit between 9:15 am and 3:30 pm IST")
    c = rec.get("contract") or {"tradingsymbol": rec["tradingsymbol"]}
    if price is None:
        live = broker.live(c, None)
        if not live:
            raise BrokerError(f"No live quote from {broker.label}: type the price to sell at")
        price = live.get("bid") or live.get("ltp")
    for gid in rec.get("gtt_ids", []):
        try:
            broker.cancel_exit(gid, c)
            rec["events"].append(f"Exit order {gid} cancelled")
        except BrokerError as exc:
            rec["events"].append(f"Exit order {gid} not cancelled: {exc}")
    oid = broker.place_order(c, "SELL", held, tick(price, float(c.get("tick_size") or TICK)))
    rec.setdefault("manual_exit_ids", []).append(oid)
    rec["events"].append(f"SELL {held} @ {tick(price):.2f} sent (order {oid})")
    return rec
