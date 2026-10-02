"""Paper trading: a virtual account to practise with real prices and realistic costs.

Instruments
  stock      NSE shares. "delivery" (buy and hold, pay in full) or "intraday" (long or short, 20% margin,
             squared off at 3:20 pm)
  option     NSE stock options, buying only. Priced with Black-Scholes from the live share price at the
             volatility fixed when you bought (no free live NSE option prices); settled at expiry
  crypto     coins quoted in US dollars, bought outright, converted to rupees at the live USD/INR rate
  commodity  MCX mini contracts sized from the US futures price, long or short, 10% margin, squared off
             at 11:15 pm

Fills are market orders at the latest Yahoo price (NSE prices are about 15 minutes delayed) plus a small
slippage. Charges follow Indian brokerage, STT/CTT, exchange, GST and stamp-duty rates, simplified.
Stop-loss and target are levels on the underlying price; every refresh walks the 5-minute bars since
entry and closes the position at the first level touched (stop first when both are in one bar).
Everything lives in ~/.stock_agent/paper.json and can be reset at any time.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))
SLIPPAGE = {"stock": 0.0005, "option": 0.01, "crypto": 0.001, "commodity": 0.0002}
MARGIN = {("stock", "intraday"): 0.20, ("commodity", "intraday"): 0.10, ("commodity", "positional"): 0.10}
SQUARE_OFF = {"stock": (15, 20), "option": (15, 20), "commodity": (23, 15)}
PRODUCTS = {"stock": ("delivery", "intraday"), "option": ("positional", "intraday"), "crypto": ("positional",),
            "commodity": ("positional", "intraday")}
HOURS = {"stock": ((9, 15), (15, 30)), "option": ((9, 15), (15, 30)), "commodity": ((9, 0), (23, 30))}


class PaperError(ValueError):
    pass


def now_ist() -> datetime:
    return datetime.now(IST)


def market_open(instrument: str, t: datetime | None = None) -> bool:
    if instrument == "crypto":
        return True
    t = t or now_ist()
    (oh, om), (ch, cm) = HOURS[instrument]
    return t.weekday() < 5 and (oh, om) <= (t.hour, t.minute) < (ch, cm)


def charges(instrument: str, product: str, side: str, value: float) -> float:
    """Approximate Indian trading charges for one order of `value` rupees."""
    if value <= 0:
        return 0.0
    if instrument == "crypto":
        return value * 0.0025                                   # exchange fee + spread, per side
    if instrument == "stock" and product == "delivery":
        brokerage = 0.0
        stt = value * 0.001                                     # both sides
        exch = value * 0.0000297
        stamp = value * 0.00015 if side == "buy" else 0.0
    elif instrument == "stock":
        brokerage = min(20.0, value * 0.0003)
        stt = value * 0.00025 if side == "sell" else 0.0
        exch = value * 0.0000297
        stamp = value * 0.00003 if side == "buy" else 0.0
    elif instrument == "option":
        brokerage = 20.0
        stt = value * 0.001 if side == "sell" else 0.0          # on the premium
        exch = value * 0.0003503
        stamp = value * 0.00003 if side == "buy" else 0.0
    else:  # commodity futures
        brokerage = min(20.0, value * 0.0003)
        stt = value * 0.0001 if side == "sell" else 0.0         # CTT, non-agri
        exch = value * 0.000021
        stamp = value * 0.00002 if side == "buy" else 0.0
    sebi = value * 0.000001
    gst = 0.18 * (brokerage + exch + sebi)
    return round(brokerage + stt + exch + stamp + sebi + gst, 2)


# --------------------------------------------------------------------------- prices
class Prices:
    """Latest prices and recent 5-minute bars, cached for a minute."""

    def __init__(self, ttl: float = 60.0):
        self.ttl = ttl
        self._bars: dict[str, tuple[float, pd.DataFrame]] = {}

    def bars(self, symbol: str) -> pd.DataFrame:
        hit = self._bars.get(symbol)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        import logging

        import yfinance as yf

        from .data import _flatten

        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        raw = yf.download(symbol, period="5d", interval="5m", progress=False, auto_adjust=False)
        if raw is None or raw.empty:
            raw = yf.download(symbol, period="1mo", interval="1d", progress=False, auto_adjust=False)
        if raw is None or raw.empty:
            raise PaperError(f"No price found for {symbol}")
        df = _flatten(raw)
        idx = pd.DatetimeIndex(raw.index)
        df.index = idx.tz_convert(IST) if idx.tz is not None else idx.tz_localize(IST)
        self._bars[symbol] = (time.time(), df)
        return df

    def last(self, symbol: str) -> tuple[float, str]:
        df = self.bars(symbol)
        return float(df["Close"].iloc[-1]), df.index[-1].strftime("%d %b %H:%M")

    def usd_inr(self) -> float:
        try:
            return self.last("USDINR=X")[0]
        except Exception:
            return 95.0


PRICES = Prices()


# --------------------------------------------------------------------------- account
def _default(start_cash: float) -> dict:
    return {"start_cash": start_cash, "cash": start_cash, "positions": [], "closed": [], "orders": [],
            "equity": [{"t": now_ist().isoformat(timespec="minutes"), "equity": start_cash}],
            "created": now_ist().isoformat(timespec="minutes")}


class Account:
    def __init__(self, path: Path, start_cash: float = 200_000, prices: Prices | None = None, cache_dir: Path | None = None):
        self.path = Path(path)
        self.prices = prices or PRICES
        self.cache_dir = cache_dir
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = _default(start_cash)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def reset(self, start_cash: float) -> dict:
        if not (1_000 <= start_cash <= 1e9):
            raise PaperError("Starting balance must be between ₹1,000 and ₹100 crore")
        self.data = _default(float(start_cash))
        self.save()
        return self.state(refresh=False)

    # ---- pricing of one instrument, in rupees per unit of quantity
    def _unit_price(self, pos: dict, spot: float | None = None, at: datetime | None = None) -> float:
        inst = pos["instrument"]
        if spot is None:
            spot = self.prices.last(pos["symbol"])[0]
        if inst == "option":
            from .options import bs_price

            at = at or now_ist()
            if at.tzinfo is None:
                at = at.replace(tzinfo=IST)
            close = datetime.combine(date.fromisoformat(pos["expiry"]), datetime.min.time(), IST) + timedelta(hours=15, minutes=30)
            t = max((close - at).total_seconds(), 0) / (365 * 86400)
            kind = "call" if pos["option_type"] == "CE" else "put"
            if t <= 0:
                return max(spot - pos["strike"], 0.0) if kind == "call" else max(pos["strike"] - spot, 0.0)
            return max(bs_price(spot, pos["strike"], t, pos["iv"], kind, 0.065), 0.05)
        if inst == "crypto":
            return spot * self.prices.usd_inr()
        if inst == "commodity":
            return spot * self.prices.usd_inr() * pos["yahoo_units_per_unit"]
        return spot

    # ---- order preview / placement
    def preview(self, o: dict) -> dict:
        from .markets import CRYPTO, MCX

        inst = o.get("instrument", "stock")
        sym = str(o.get("symbol", "")).strip().upper()
        side = o.get("side", "buy")
        product = o.get("product", "delivery" if inst == "stock" else "positional")
        if inst not in SLIPPAGE:
            raise PaperError("Unknown instrument")
        if not sym:
            raise PaperError("Enter a symbol")
        if inst == "stock" and not sym.endswith((".NS", ".BO")) and "." not in sym and "-" not in sym:
            sym += ".NS"
        if inst == "option" and not sym.endswith(".NS"):
            sym += ".NS"
        if side not in ("buy", "sell"):
            raise PaperError("Side must be buy or sell")
        if product not in PRODUCTS[inst]:
            raise PaperError(f"Product for {inst} must be one of: {', '.join(PRODUCTS[inst])}")
        short = side == "sell"
        if short and not (inst == "commodity" or (inst == "stock" and product == "intraday")):
            raise PaperError("Selling first (short) is only allowed for intraday shares and commodity futures")
        spot, asof = self.prices.last(sym)
        entry_bar = self.prices.bars(sym).index[-1].isoformat()
        pos = {"instrument": inst, "symbol": sym, "side": "short" if short else "long", "product": product}
        lot = 1
        if inst == "option":
            from .lots import lot_size
            from .options import historical_vol
            from .sizing import nse_monthly_expiry, strike_step

            try:
                from .data import load_prices
                hist = load_prices(sym, "1y", "1d", cache_dir=self.cache_dir)["Close"] if self.cache_dir else \
                    load_prices(sym, "1y", "1d")["Close"]
                iv = max(historical_vol(hist, 20), historical_vol(hist, 60)) * 1.1
            except Exception:
                iv = 0.30
            expiry = date.fromisoformat(o["expiry"]) if o.get("expiry") else nse_monthly_expiry(now_ist().date(), 10)
            strike = float(o.get("strike") or round(spot / strike_step(spot)) * strike_step(spot))
            lot, _ = lot_size(sym, expiry, cache_dir=self.cache_dir) if self.cache_dir else lot_size(sym, expiry)
            if not lot:
                raise PaperError(f"{sym} has no options on NSE")
            pos.update(option_type=("PE" if o.get("option_type") == "PE" else "CE"), strike=strike,
                       expiry=expiry.isoformat(), iv=round(float(iv), 4), lot_size=lot)
        if inst == "commodity":
            if sym not in MCX:
                raise PaperError("Choose gold, silver, crude oil, natural gas or copper")
            contract, per_lot, yahoo_units, label = MCX[sym]
            lot = per_lot
            pos.update(contract=contract, lot_size=per_lot, yahoo_units_per_unit=yahoo_units, unit_label=label)
        if inst == "crypto" and sym not in CRYPTO and not sym.endswith("-USD"):
            raise PaperError("Use a coin symbol such as BTC-USD")
        qty = float(o.get("qty") or 0)
        if inst in ("option", "commodity"):
            lots = int(o.get("lots") or 0)
            if lots <= 0:
                raise PaperError("Enter the number of lots")
            qty = lots * lot
        elif inst == "stock":
            qty = int(qty)
        if qty <= 0:
            raise PaperError("Enter a quantity above zero")
        unit = self._unit_price(pos, spot)
        slip = SLIPPAGE[inst]
        fill = unit * (1 - slip if short else 1 + slip)
        value = fill * qty
        margin_rate = MARGIN.get((inst, product), 1.0)
        blocked = value * margin_rate
        fee = charges(inst, product, side, value)
        stop = float(o["stop"]) if o.get("stop") not in (None, "") else None
        target = float(o["target"]) if o.get("target") not in (None, "") else None
        if stop is not None and ((stop >= spot) if not short else (stop <= spot)):
            raise PaperError("The stop-loss must be below the price for a buy (above it for a sell)")
        if target is not None and ((target <= spot) if not short else (target >= spot)):
            raise PaperError("The target must be above the price for a buy (below it for a sell)")
        warnings = []
        if not market_open(inst):
            warnings.append("Market is closed: orders fill only during market hours")
        if blocked + fee > self.data["cash"]:
            warnings.append(f"Not enough virtual cash: needs ₹{blocked + fee:,.0f}, you have ₹{self.data['cash']:,.0f}")
        risk = abs(spot - stop) * qty * (fill / spot if inst != "option" else 1) if stop else None
        if inst == "option" and stop:
            risk = (fill - self._unit_price(pos, stop)) * qty
        return {**pos, "qty": qty, "lots": qty / lot if lot > 1 else None, "spot": spot, "price_time": asof,
                "fill": round(fill, 4), "value": round(value, 2), "blocked": round(blocked, 2), "charges": fee,
                "margin_rate": margin_rate, "stop": stop, "target": target, "risk_at_stop": risk,
                "exit_by": o.get("exit_by") or None, "entry_bar": entry_bar, "warnings": warnings, "can_place": not warnings}

    def place(self, o: dict) -> dict:
        pv = self.preview(o)
        if not pv["can_place"]:
            raise PaperError("; ".join(pv["warnings"]))
        pos = {k: pv[k] for k in pv if k not in ("warnings", "can_place", "spot", "price_time", "value", "lots",
                                                  "risk_at_stop")}
        pos.update(id=uuid.uuid4().hex[:8], entry_price=pv["fill"], entry_spot=pv["spot"],
                   entry_time=now_ist().isoformat(timespec="minutes"), entry_charges=pv["charges"],
                   note=str(o.get("note") or "")[:120])
        self.data["cash"] = round(self.data["cash"] - pv["blocked"] - pv["charges"], 2)
        self.data["positions"].append(pos)
        self.data["orders"].append({"t": pos["entry_time"], "id": pos["id"], "action": "open",
                                    "text": f"{'BUY' if pos['side'] == 'long' else 'SELL'} {self._label(pos)} @ {pv['fill']:,.2f}"})
        self._snapshot()
        self.save()
        return self.state(refresh=False)

    def _label(self, p: dict) -> str:
        base = p["symbol"].replace(".NS", "")
        if p["instrument"] == "option":
            return f"{base} {date.fromisoformat(p['expiry']):%d%b} {p['strike']:g} {p['option_type']} × {p['qty']:g}"
        if p["instrument"] == "commodity":
            return f"{p['contract']} × {p['qty'] / p['lot_size']:g} lot"
        return f"{base} × {p['qty']:g}"

    # ---- closing
    def _close(self, p: dict, unit_price: float, reason: str, at: datetime | None = None) -> None:
        short = p["side"] == "short"
        slip = SLIPPAGE[p["instrument"]]
        fill = unit_price * (1 + slip if short else 1 - slip)
        qty = p["qty"]
        pnl_gross = (p["entry_price"] - fill) * qty if short else (fill - p["entry_price"]) * qty
        fee = charges(p["instrument"], p["product"], "buy" if short else "sell", fill * qty)
        blocked = p["blocked"]
        self.data["cash"] = round(self.data["cash"] + blocked + pnl_gross - fee, 2)
        net = pnl_gross - fee - p["entry_charges"]
        t = (at or now_ist()).isoformat(timespec="minutes")
        self.data["closed"].insert(0, {**p, "exit_price": round(fill, 4), "exit_time": t, "exit_reason": reason,
                                       "exit_charges": fee, "pnl": round(net, 2),
                                       "return_pct": net / (p["entry_price"] * qty) if p["entry_price"] else 0})
        self.data["positions"] = [x for x in self.data["positions"] if x["id"] != p["id"]]
        self.data["orders"].append({"t": t, "id": p["id"], "action": "close",
                                    "text": f"{reason}: {self._label(p)} @ {fill:,.2f}, P&L ₹{net:,.0f}"})

    def close(self, pid: str) -> dict:
        p = next((x for x in self.data["positions"] if x["id"] == pid), None)
        if not p:
            raise PaperError("Position not found")
        if not market_open(p["instrument"]):
            raise PaperError("Market is closed: positions can be closed during market hours")
        self._close(p, self._unit_price(p), "Closed by you")
        self._snapshot()
        self.save()
        return self.state(refresh=False)

    # ---- automatic exits: stop, target, square-off, exit date, expiry
    def process(self) -> list[str]:
        events = []
        for p in list(self.data["positions"]):
            try:
                bars = self.prices.bars(p["symbol"])
            except Exception:
                continue
            since = bars[bars.index > pd.Timestamp(p.get("entry_bar") or p["entry_time"])]
            short = p["side"] == "short"
            hit = None
            sq = SQUARE_OFF.get(p["instrument"]) if p["product"] == "intraday" else None
            entry_day = datetime.fromisoformat(p["entry_time"]).date()
            for ts, b in since.iterrows():
                o, h, l = float(b["Open"]), float(b["High"]), float(b["Low"])
                if p.get("stop") is not None and ((h >= p["stop"]) if short else (l <= p["stop"])):
                    lvl = max(o, p["stop"]) if short else min(o, p["stop"])
                    hit = ("Stop-loss hit", lvl, ts)
                    break
                if p.get("target") is not None and ((l <= p["target"]) if short else (h >= p["target"])):
                    lvl = min(o, p["target"]) if short else max(o, p["target"])
                    hit = ("Target hit", lvl, ts)
                    break
                if sq and (ts.date() > entry_day or (ts.hour, ts.minute) >= sq):
                    hit = ("Auto square-off", float(b["Open"]), ts)
                    break
            if not hit and sq and now_ist().date() > entry_day and len(since) == 0:
                hit = ("Auto square-off", float(bars["Close"].iloc[-1]), now_ist())
            if not hit and p.get("exit_by") and now_ist().date() > date.fromisoformat(p["exit_by"]):
                hit = ("Exit date reached", float(bars["Close"].iloc[-1]), now_ist())
            if not hit and p["instrument"] == "option" and now_ist().date() > date.fromisoformat(p["expiry"]):
                hit = ("Expired", float(bars["Close"].iloc[-1]), now_ist())
            if hit:
                reason, level, ts = hit
                at = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                self._close(p, self._unit_price(p, level, at), reason, at)
                events.append(f"{reason}: {self._label(p)}")
        if events:
            self._snapshot()
            self.save()
        return events

    def _snapshot(self) -> None:
        eq = self.equity()
        t = now_ist().isoformat(timespec="minutes")
        hist = self.data["equity"]
        if len(hist) > 1 and hist[-1]["t"][:15] == t[:15]:   # one point per 10 minutes; keep the starting point
            hist[-1] = {"t": t, "equity": eq}
        else:
            hist.append({"t": t, "equity": eq})

    def equity(self) -> float:
        total = self.data["cash"]
        for p in self.data["positions"]:
            try:
                unit = self._unit_price(p)
            except Exception:
                unit = p["entry_price"]
            pnl = (p["entry_price"] - unit) * p["qty"] if p["side"] == "short" else (unit - p["entry_price"]) * p["qty"]
            total += p["blocked"] + pnl
        return round(total, 2)

    def state(self, refresh: bool = True) -> dict:
        events = self.process() if refresh else []
        positions = []
        for p in self.data["positions"]:
            try:
                spot, asof = self.prices.last(p["symbol"])
                unit = self._unit_price(p, spot)
            except Exception:
                spot, asof, unit = None, None, p["entry_price"]
            pnl = (p["entry_price"] - unit) * p["qty"] if p["side"] == "short" else (unit - p["entry_price"]) * p["qty"]
            positions.append({**p, "label": self._label(p), "spot": spot, "price_time": asof, "now": round(unit, 4),
                              "pnl": round(pnl - p["entry_charges"], 2)})
        if refresh:
            self._snapshot()
            self.save()
        closed = self.data["closed"]
        wins = [c for c in closed if c["pnl"] > 0]
        eq = self.equity()
        curve = [x["equity"] for x in self.data["equity"]]
        peak, dd = 0.0, 0.0
        for v in curve:
            peak = max(peak, v)
            dd = min(dd, v / peak - 1 if peak else 0)
        return {"start_cash": self.data["start_cash"], "cash": self.data["cash"], "equity": eq,
                "pnl": round(eq - self.data["start_cash"], 2), "return_pct": eq / self.data["start_cash"] - 1,
                "positions": positions, "closed": [{**c, "label": self._label(c)} for c in closed[:200]],
                "trades": len(closed), "wins": len(wins), "win_rate": len(wins) / len(closed) if closed else None,
                "avg_win": sum(c["pnl"] for c in wins) / len(wins) if wins else None,
                "avg_loss": (sum(c["pnl"] for c in closed if c["pnl"] <= 0) / max(len(closed) - len(wins), 1)) if len(closed) > len(wins) else None,
                "charges_paid": round(sum(c["entry_charges"] + c["exit_charges"] for c in closed)
                                      + sum(p["entry_charges"] for p in self.data["positions"]), 2),
                "max_drawdown": dd, "equity_curve": self.data["equity"][-500:], "events": events,
                "orders": self.data["orders"][-30:][::-1], "created": self.data["created"]}
