"""A small local web app around the agent: `python -m stock_agent app` opens it in your browser.

Runs entirely on your machine with the Python standard library (no extra installs). Everything it
stores lives in ~/.stock_agent (settings, trade journal and the price/news cache), so it works the same
whichever folder you start it from.
"""
from __future__ import annotations

import json
import math
import os
import secrets
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

HOME = Path(os.environ.get("STOCK_AGENT_HOME", Path.home() / ".stock_agent"))
CACHE = HOME / "cache"
SETTINGS_FILE = HOME / "settings.json"
JOURNAL_FILE = HOME / "journal.json"
HOLDINGS_FILE = HOME / "holdings.json"
SALES_FILE = HOME / "holdings_sales.json"
ALERTS_FILE = HOME / "alerts.json"
PENNY_FILE = HOME / "penny.json"
LEDGER_FILE = HOME / "track.json"
GOAL_FILE = HOME / "goal.json"
PORTFOLIO_FILE = HOME / "portfolio.json"
PAPER_FILE = HOME / "paper.json"
ROBUST_FILE = HOME / "robust.json"
WEB = Path(__file__).with_name("web")

DEFAULT_SETTINGS = {
    "capital": 200000, "risk": 2.0, "universe": "nifty50", "custom": "", "horizon": 5,
    "affordable_only": False, "top": 10, "broker": "kite",
    "brokerage": 0.0,          # delivery brokerage per order in ₹ (0 at Zerodha; up to 20 elsewhere)
    # automatic penny-stock and penny-coin screens: rule overrides stay empty until you change them in the tab
    "penny": {"auto": True, "stocks": {}, "crypto": {}},
}

_lock = threading.Lock()
_jobs: dict[str, dict] = {}
# Per-run secret embedded in the page. Every request that changes something must carry it, so another
# website open in the same browser cannot drive this local server (and never your Kite account).
SESSION_TOKEN = secrets.token_urlsafe(24)
_kite = None
# Phone mode: the server also listens on the Wi-Fi network. Other devices must present this key once
# (from the link printed at start-up); it is then kept in a cookie. It is stored so that a phone's
# home-screen icon keeps working after restarts.
LISTEN_PORT = 0
PHONE = {"on": False, "key": None, "urls": [], "tunnel_host": None, "tunnel_url": None}
PHONE_KEY_FILE = HOME / "phone_key.txt"


def _phone_key(min_len: int = 8) -> str:
    """The stored access key; a longer one is made when the app is reachable from the internet."""
    try:
        k = PHONE_KEY_FILE.read_text(encoding="utf-8").strip()
        if len(k) >= min_len:
            return k
    except Exception:
        pass
    k = secrets.token_urlsafe(6 if min_len <= 8 else 12)
    PHONE_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    PHONE_KEY_FILE.write_text(k, encoding="utf-8")
    return k


def _lan_ips() -> list[str]:
    import socket

    ips = set()
    try:  # the address used to reach the internet (no packet is actually sent for a UDP connect)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))
            ips.add(sk.getsockname()[0])
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def _icon_png(size: int) -> bytes:
    """The app icon (blue rounded square, three white candles) as a PNG, drawn without any image library."""
    import struct
    import zlib

    r = size * 0.22
    blue, white = (42, 120, 214), (255, 255, 255)
    bars = [(0.30, 0.20, 0.80), (0.50, 0.30, 0.82), (0.70, 0.14, 0.62)]  # (x centre, top, bottom) as fractions
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            # rounded-corner mask
            cx = min(max(x, r), size - 1 - r)
            cy = min(max(y, r), size - 1 - r)
            inside = (x - cx) ** 2 + (y - cy) ** 2 <= r * r
            px = (0, 0, 0, 0)
            if inside:
                px = (*blue, 255)
                for bx, top, bot in bars:
                    if abs(x - bx * size) <= size * 0.018 and top * size <= y <= bot * size:
                        px = (*white, 255)
                    if abs(x - bx * size) <= size * 0.065 and (top + 0.12) * size <= y <= (bot - 0.12) * size:
                        px = (*white, 255)
            row.extend(px)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def qr_svg(text: str) -> bytes:
    """A QR code of the phone link as SVG, drawn locally (no internet needed)."""
    import io

    import qrcode
    import qrcode.image.svg

    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathFillImage, box_size=10, border=2,
                      error_correction=qrcode.constants.ERROR_CORRECT_M)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()


_ICONS: dict[int, bytes] = {}
MANIFEST = {
    "name": "Stock Agent", "short_name": "Stock Agent", "start_url": "/", "scope": "/", "display": "standalone",
    "background_color": "#f6f6f3", "theme_color": "#2a78d6",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
}


_brokers: dict = {}


def broker_client(key: str | None = None):
    """The broker adapter for `key` (default: the one chosen in Settings), one instance per broker."""
    from . import brokers

    key = key or load_settings().get("broker") or "kite"
    if key not in brokers.BROKERS:
        key = "kite"
    if key not in _brokers:
        _brokers[key] = brokers.make(key, HOME)
    return _brokers[key]


def kite_client():
    """The selected broker (named for the first one, Kite)."""
    return broker_client()


def broker_redirect(key: str, port: int) -> str:
    return f"http://127.0.0.1:{port}/kite/callback" if key == "kite" else f"http://127.0.0.1:{port}/broker/callback/{key}"


# --------------------------------------------------------------------------- storage helpers
def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_settings() -> dict:
    return {**DEFAULT_SETTINGS, **_read_json(SETTINGS_FILE, {})}


def clean(obj):
    """Make anything JSON-safe: dataclasses, dates, numpy scalars, NaN/inf -> None."""
    if is_dataclass(obj):
        obj = asdict(obj)
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return obj.isoformat()[:10]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


# --------------------------------------------------------------------------- background jobs
def start_job(kind: str, fn, *args) -> str:
    job_id = uuid.uuid4().hex[:10]
    job = {"id": job_id, "kind": kind, "status": "running", "message": "Starting", "progress": 0.0,
           "result": None, "error": None, "started": time.time()}
    with _lock:
        _jobs[job_id] = job

    def progress(msg, frac):
        job["message"], job["progress"] = msg, round(float(frac), 3)

    def run():
        try:
            job["result"] = clean(fn(*args, progress=progress))
            job["status"] = "done"
        except Exception as exc:  # surfaced to the UI
            traceback.print_exc()
            job["status"], job["error"] = "error", f"{type(exc).__name__}: {exc}"

    threading.Thread(target=run, daemon=True).start()
    return job_id


def plan_dict(plan) -> dict | None:
    if plan is None:
        return None
    from .sizing import money

    d = clean(plan)
    d["contract"] = plan.contract
    d["risk_per_lot"] = round(max(plan.premium - plan.stop_premium, 0.05) * (plan.lot_size or 0), 2)
    d["budget"] = plan.capital * plan.risk_pct
    d["cost_text"] = money(plan.cost, plan.currency)
    d["estimated"] = "estimated" in plan.premium_source
    return d


def run_screen(settings: dict, progress) -> dict:
    from . import screener
    from .universes import get_universe

    if settings.get("universe") == "custom":
        tickers = [t.strip().upper() for t in str(settings.get("custom", "")).replace(";", ",").split(",") if t.strip()]
        if not tickers:
            raise ValueError("Add some symbols to your custom list in Settings, e.g. INFY.NS, TCS.NS")
    else:
        try:
            tickers = get_universe(settings.get("universe", "nifty50"))
        except KeyError:
            tickers = get_universe("nifty50")
    picks, ranked, stats = screener.screen(
        tickers, horizon=int(settings["horizon"]), top=int(settings["top"]), cache_dir=CACHE,
        capital=float(settings["capital"]), risk_pct=float(settings["risk"]) / 100,
        affordable_only=bool(settings.get("affordable_only")), log=lambda *a: None, progress=progress,
    )
    from . import tracker

    tracker.record(LEDGER_FILE, picks, stats)  # every scan feeds the track record
    out = []
    for p in picks:
        d = clean(p)
        d.pop("plan", None)
        d.update(side=p.side, edge=p.edge, win_chance=p.win_chance, plan=plan_dict(p.plan),
                 meta=meta_filter(p.ticker, p.pattern, p.direction))
        out.append(d)
    from .screener import validation_line
    from .tradetest import load_validation

    return {"picks": out, "stats": stats, "settings": settings, "generated": datetime.now().isoformat(timespec="minutes"),
            "validation": load_validation(), "validation_text": validation_line()}


def run_track(settings: dict, replay_days: int, progress) -> dict:
    from . import screener, tracker
    from .universes import get_universe

    if replay_days:
        name = settings.get("universe", "nifty50")
        tickers = (get_universe(name if name != "nse_all" else "fno") if name != "custom"   # replaying 2,000 stocks
                   else [t.strip().upper() for t in str(settings.get("custom", "")).split(",") if t.strip()])
        progress("Loading prices", 0.05)
        prices = screener.load_universe(tickers, "10y", "1d", False, CACHE, log=lambda *a: None,
                                        progress=lambda m, f: progress(m, 0.05 + f * 0.4))
        tracker.replay(tickers, replay_days, prices, LEDGER_FILE, int(settings["horizon"]), int(settings["top"]),
                       float(settings["capital"]), float(settings["risk"]) / 100, CACHE,
                       progress=lambda m, f: progress(m, 0.45 + f * 0.5))
    rows = tracker.load_ledger(LEDGER_FILE)
    need = sorted({r["ticker"] for r in rows})
    progress("Updating prices for tracked picks", 0.95)
    prices = screener.load_universe(need, "10y", "1d", False, CACHE, log=lambda *a: None) if need else {}
    ev = tracker.evaluate(rows, prices)
    for x in ev["results"]:
        x.pop("daily_values", None)
    progress("Done", 1.0)
    return ev


def run_market(market: str, settings: dict, progress) -> dict:
    from . import markets

    res = markets.scan(market, float(settings["capital"]), float(settings["risk"]) / 100, False, CACHE,
                       progress=progress)
    rows = [clean(r) for r in res["rows"]]
    if market == "crypto":
        progress("Trade ideas: replaying each one over the coin's history", 0.95)
        for r in rows:
            r["idea"] = crypto_idea(r["symbol"], float(settings["capital"]), float(settings["risk"]) / 100,
                                    res.get("usd_inr") or 90.0)
        rows.sort(key=lambda r: -((r.get("idea") or {}).get("chance") or 0))
    return {**res, "rows": rows, "check_text": markets.check_line(res)}


def crypto_idea(symbol: str, capital: float, risk_pct: float, usd_inr: float) -> dict | None:
    """A concrete long trade for a coin: stop 2 x ATR below, target 3 x ATR above, hold up to 10 days, sized so the
    stop loses at most the risk budget, with its win rate replayed over the coin's history."""
    from . import tradecheck
    from .data import _cache_path, read_cached
    from .sizing import atr

    try:
        df = read_cached(_cache_path(CACHE, symbol, "10y", "1d"))
        last, a = float(df["Close"].iloc[-1]), float(atr(df, 14))
        stop, target = last - 2 * a, last + 3 * a
        if stop <= 0:
            return None
        res = tradecheck.check(df, "crypto", "buy", last, stop, target, 10)
    except Exception:
        return None
    al, lk = res["all"], res["like_today"]
    per_coin_risk = (last - stop) * usd_inr
    units = (capital * risk_pct) / per_coin_risk if per_coin_risk > 0 else 0
    units = min(units, capital * 0.25 / (last * usd_inr))           # never more than a quarter of the capital
    chance = (lk or al)["win_rate"]
    return {"entry": last, "stop": stop, "target": target, "hold_days": 10, "atr": a, "units": round(units, 6),
            "value_inr": units * last * usd_inr, "risk_inr": units * per_coin_risk, "chance": chance,
            "win_all": al["win_rate"], "trades_all": al["trades"], "win_like": lk["win_rate"] if lk else None,
            "trades_like": lk["trades"] if lk else 0, "avg_ret": al["avg_ret"], "breakeven": res["breakeven"],
            "trend": res["today"]["trend"], "volatility": res["today"]["volatility"]}


def run_goal(body: dict, progress) -> dict:
    from . import planner

    fields = planner.Goal.__dataclass_fields__
    g = planner.Goal(**{k: (str(v) if k in ("name", "profile") else float(v)) for k, v in body.items() if k in fields})
    if not (0.5 <= g.years <= 50):
        raise ValueError("Years must be between 0.5 and 50")
    progress("Reading ten years of Nifty and gold prices", 0.2)
    stats = planner.market_stats(False, CACHE)
    progress("Simulating 5,000 market paths", 0.5)
    p = planner.plan(g, stats=stats)
    _write_json(GOAL_FILE, clean(p["goal"]))
    progress("Done", 1.0)
    return clean(p)


def run_rebalance(body: dict, progress) -> dict:
    from . import rebalance as R

    rows = [R.Holding(name=str(h.get("name") or ""), asset_class=str(h.get("asset_class") or "other"),
                      value=float(h["value"]) if h.get("value") not in (None, "") else None,
                      symbol=(str(h.get("symbol")).strip() or None) if h.get("symbol") else None,
                      quantity=float(h["quantity"]) if h.get("quantity") not in (None, "") else None)
            for h in body.get("holdings", []) if any(h.get(k) for k in ("name", "symbol", "value"))]
    target = {k: float(v) for k, v in (body.get("target") or {}).items() if float(v or 0) > 0}
    if not target:
        raise ValueError("Set a target mix that adds up to 100%")
    progress("Fetching today's prices", 0.3)
    priced = R.price_holdings(rows, False, CACHE)
    rec = R.recommend(priced, target, float(body.get("band", 5)) / 100, float(body.get("new_money") or 0))
    _write_json(PORTFOLIO_FILE, {"holdings": body.get("holdings", []), "target": body.get("target"),
                                 "band": body.get("band", 5), "new_money": body.get("new_money", 0)})
    progress("Done", 1.0)
    return clean({**rec, "holdings": priced})


def run_fund(body: dict, settings: dict, progress) -> dict:
    from . import fund

    from .attribution import BENCHMARKS

    rules = fund.FundRules(universe=body.get("universe", "nifty200"), size=int(body.get("size", 25)),
                           rebalance=body.get("rebalance", "Q"), benchmark=body.get("benchmark") or "NIFTYBEES.NS")
    from . import pit

    if (rules.universe not in ("nifty200", "nifty100", "nifty500", "fno") and not pit.size(rules.universe)) or not (10 <= rules.size <= 40) \
            or rules.rebalance not in ("M", "Q", "H", "Y") or rules.benchmark not in BENCHMARKS:
        raise ValueError("Unsupported fund settings")
    res = fund.run(rules, float(settings["capital"]), False, CACHE, progress=progress,
                   per_order=float(settings.get("brokerage") or 0))
    curve = res.pop("curve")
    res["curve"] = [{"date": str(d.date()), **{k: float(v) for k, v in row.items()}} for d, row in curve.iterrows()]
    res.pop("log", None)
    res.pop("daily", None)
    return clean(res)


_paper_lock = threading.Lock()


def paper_account():
    from .paper import Account

    return Account(PAPER_FILE, start_cash=float(load_settings()["capital"]), cache_dir=CACHE)


def run_paper(progress) -> dict:
    progress("Fetching live prices for your open positions", 0.3)
    with _paper_lock:
        return paper_account().state(refresh=True)


def paper_op(path: str, body: dict) -> dict:
    with _paper_lock:
        acct = paper_account()
        if path == "/api/paper/preview":
            return acct.preview(body)
        if path == "/api/paper/order":
            return acct.place(body)
        if path.startswith("/api/paper/close/"):
            return acct.close(path.rsplit("/", 1)[-1])
        if path == "/api/paper/reset":
            return acct.reset(float(body.get("start_cash") or load_settings()["capital"]))
    raise KeyError(path)


def run_robust(settings: dict, progress) -> dict:
    """Walk-forward, meta-label, PBO, FDR and bootstrap checks on the Nifty 50 signals, as options and as shares."""
    from . import robust, screener
    from .data import _cache_path, read_cached, refresh_many
    from .universes import get_universe

    tickers = get_universe("nifty50")
    prices = screener.load_universe(tickers, "10y", "1d", False, CACHE, log=lambda *a: None,
                                    progress=lambda m, f: progress(m, f * 0.3))
    refresh_many(["NIFTYBEES.NS"], "10y", "1d", CACHE)
    idx = read_cached(_cache_path(CACHE, "NIFTYBEES.NS", "10y", "1d"))
    opt = robust.run(prices, idx, lambda m, f: progress("Option trades: " + m, 0.3 + f * 0.35), instrument="option")
    spot = robust.run(prices, idx, lambda m, f: progress("Share trades: " + m, 0.65 + f * 0.35), instrument="spot",
                      cost=0.001)
    res = clean({"option": opt, "spot": spot})
    _write_json(ROBUST_FILE, res)
    return res


def meta_filter(ticker: str, pattern: str, direction: str) -> dict | None:
    """The meta-label model's view of one pick, if the robust study validated the filter."""
    saved = _read_json(ROBUST_FILE, None)
    model = ((saved or {}).get("option") or {}).get("model")
    if not model or not model.get("validated"):
        return None
    from . import robust
    from .data import _cache_path, read_cached

    try:
        df = read_cached(_cache_path(CACHE, ticker, "10y", "1d"))
        idx = read_cached(_cache_path(CACHE, "NIFTYBEES.NS", "10y", "1d"))
    except Exception:
        return None
    p = robust.score_today(model, df, idx, pattern, direction)
    return None if p is None else {"p": p, "keep": p >= model["threshold"], "threshold": model["threshold"]}


def run_check(body: dict, progress) -> dict:
    """Historical win rate of a trade the person describes (shares, options, crypto, commodities)."""
    from . import tradecheck
    from .data import load_prices
    from .sizing import nse_monthly_expiry, strike_step

    inst = body.get("instrument", "stock")
    if inst not in ("stock", "option", "crypto", "commodity"):
        raise ValueError("Choose shares, options, crypto or a commodity")
    sym = str(body.get("symbol") or "").strip().upper()
    if not sym:
        raise ValueError("Enter a symbol")
    if inst in ("stock", "option") and "." not in sym:
        sym += ".NS"
    product = body.get("product") or ("delivery" if inst == "stock" else "positional")
    intraday = product == "intraday"
    interval, period = ("15m", "60d") if intraday else ("1d", "10y")
    progress(f"Loading {'15-minute' if intraday else 'daily'} prices for {sym}", 0.2)
    df = load_prices(sym, period, interval, cache_dir=CACHE)
    num = lambda k: float(body[k]) if body.get(k) not in (None, "") else None
    entry = num("entry") or float(df["Close"].iloc[-1])
    hold = int(num("hold_hours") * 4) if intraday and num("hold_hours") else int(num("hold_days") or (6 if intraday else 5))
    hold = max(hold, 1)
    option = None
    if inst == "option":
        expiry = date.fromisoformat(body["expiry"]) if body.get("expiry") else nse_monthly_expiry(date.today(), 10)
        strike = num("strike") or round(entry / strike_step(entry)) * strike_step(entry)
        option = {"type": "PE" if body.get("option_type") == "PE" else "CE", "strike": strike,
                  "days_to_expiry": max((expiry - date.today()).days, 1), "expiry": expiry.isoformat()}
    progress("Replaying the trade from every past day", 0.5)
    res = tradecheck.check(df, inst, body.get("side", "buy"), entry, num("stop"), num("target"), hold,
                           product, option, interval)
    progress("Done", 1.0)
    return clean({**res, "symbol": sym, "instrument": inst, "product": product, "side": body.get("side", "buy"),
                  "entry": entry, "stop": num("stop"), "target": num("target"), "option": option,
                  "last_price": float(df["Close"].iloc[-1]), "hold": hold})


def run_myscreen(body: dict, settings: dict, progress) -> dict:
    from . import fund, myscreen

    from . import pit

    uni = body.get("universe", "nifty200")
    if uni not in ("nifty50", "nifty100", "nifty200", "nifty500", "fno") and not pit.size(uni):
        raise ValueError("Unsupported universe")
    sc = myscreen.Screen(filters=list(body.get("filters") or []), rank_by=body.get("rank_by", "ret_6m"),
                         descending=bool(body.get("descending", True)), top=int(body.get("top") or 10),
                         rebalance=body.get("rebalance", "Q"))
    sc.validate()
    from .attribution import BENCHMARKS

    bm = body.get("benchmark") or "NIFTYBEES.NS"
    if bm not in BENCHMARKS:
        raise ValueError("Unknown benchmark")
    closes, values, bench, sectors = fund.load(fund.FundRules(universe=uni, benchmark=bm), False, CACHE, progress)
    fund_data = None
    if sc.uses_fundamentals():
        from . import fundamentals

        fund_data = fundamentals.fetch_many(list(closes.columns), CACHE,
                                            progress=lambda m, f: progress(m, 0.42 + 0.08 * f))
    res = myscreen.run(closes, values, bench, sc, float(settings["capital"]), sectors, progress,
                       per_order=float(settings.get("brokerage") or 0), benchmark=bm, pit_n=pit.size(uni),
                       fund_data=fund_data, cache_dir=CACHE, backtest_too=not body.get("skip_backtest"))
    if body.get("watch"):                    # alert when today's picks change (checked daily after the close)
        from . import alerts as AL

        store = AL.Store(ALERTS_FILE)
        with _alerts_lock:
            d = store.load()
            d["screen"] = {"body": {k: v for k, v in body.items() if k not in ("watch", "skip_backtest")},
                           "picks": [r["ticker"] for r in res["picks"]]}
            store.save(d)
    return clean({**res, "universe": uni})


# --------------------------------------------------------------------------- penny stocks and penny crypto
_penny_lock = threading.Lock()            # one heavy scan at a time
_penny_file_lock = threading.Lock()
_penny_running: set[str] = set()
PENNY_KEYS = {"stocks": ("price_cap", "min_lakh", "top", "bucket_pct", "uptrend"),
              "crypto": ("price_cap", "top", "bucket_pct", "uptrend")}


def _penny_clean(p) -> dict:
    """Only the choices the page offers, checked."""
    from . import pennycrypto as PC
    from . import pennystocks as PS

    p = p if isinstance(p, dict) else {}
    out = {"auto": bool(p.get("auto", True))}
    for kind, mod in (("stocks", PS), ("crypto", PC)):
        given = {k: v for k, v in (p.get(kind) or {}).items() if k in PENNY_KEYS[kind]}
        rules = asdict(mod.rules_from(given))
        out[kind] = {k: rules[k] for k in given}
    return out


def _penny_rules(kind: str, settings: dict, body: dict | None = None):
    from . import pennycrypto as PC
    from . import pennystocks as PS

    mod = PS if kind == "stocks" else PC
    base = ((settings.get("penny") or {}).get(kind) or {})
    return mod.rules_from({**base, **{k: v for k, v in (body or {}).items() if k in PENNY_KEYS[kind]}})


def _penny_remember(kind: str, body: dict) -> None:
    """Keep the choices made on the page, so the automatic evening run uses them too."""
    mine = {k: v for k, v in (body or {}).items() if k in PENNY_KEYS[kind]}
    if not mine:
        return
    s = _read_json(SETTINGS_FILE, {})
    pen = _penny_clean({**(s.get("penny") or {}), kind: {**((s.get("penny") or {}).get(kind) or {}), **mine}})
    s["penny"] = pen
    _write_json(SETTINGS_FILE, s)


def _penny_after_close(t) -> bool:
    return t.weekday() < 5 and (t.hour, t.minute) >= (16, 15)


def _penny_save(kind: str, res: dict, today: date | None = None, now=None) -> dict:
    """Store the latest scan, work out what changed since the last day's list, and how long each pick has been on it."""
    from .paper import now_ist

    today = today or date.today()
    res = clean(res)
    with _penny_file_lock:
        data = _read_json(PENNY_FILE, {})
        old = data.get(kind) or {}
        prev = [p["ticker"] for p in (old.get("result") or {}).get("picks", [])]
        base = old.get("baseline") or {"date": None, "picks": []}
        if old.get("date") and old["date"] != today.isoformat():         # first scan of a new day: yesterday's list is the baseline
            base = {"date": old["date"], "picks": prev}
        hist = old.get("history", {})
        now_l = [p["ticker"] for p in res["picks"]]
        new_hist = {t: {"first": (hist.get(t) or {}).get("first", today.isoformat())} for t in now_l}
        for p in res["picks"]:
            p["first_seen"] = new_hist[p["ticker"]]["first"]
            p["days_on_list"] = (today - date.fromisoformat(p["first_seen"])).days + 1
        res["changes"] = {"first": not old, "since": base["date"], "new": [t for t in now_l if t not in base["picks"]] if base["date"] else [],
                          "out": [t for t in base["picks"] if t not in now_l]}
        data[kind] = {"result": res, "history": new_hist, "baseline": base, "date": today.isoformat()}
        t = now or now_ist()
        if kind == "crypto" or _penny_after_close(t):                      # a scan after the close counts as the evening run
            data.setdefault("auto", {})[kind] = today.isoformat()
        _write_json(PENNY_FILE, data)
    return res


def run_penny_stocks(body: dict, settings: dict, progress) -> dict:
    from . import pennystocks as PS

    rules = _penny_rules("stocks", settings, body)
    with _penny_lock:
        res = PS.scan(rules, float(settings["capital"]), CACHE, progress)
    return _penny_save("stocks", res)


def run_penny_crypto(body: dict, settings: dict, progress) -> dict:
    from . import pennycrypto as PC

    rules = _penny_rules("crypto", settings, body)
    with _penny_lock:
        res = PC.scan(rules, float(settings["capital"]), CACHE, progress)
    return _penny_save("crypto", res)


def run_penny_test(body: dict, settings: dict, progress) -> dict:
    from . import pennystocks as PS

    rules = _penny_rules("stocks", settings, body)
    with _penny_lock:
        res = clean(PS.backtest(rules, float(settings["capital"]), CACHE, progress, per_order=float(settings.get("brokerage") or 0)))
    with _penny_file_lock:
        data = _read_json(PENNY_FILE, {})
        data["test"] = {"result": res, "date": date.today().isoformat()}
        _write_json(PENNY_FILE, data)
    return res


def penny_state() -> dict:
    data = _read_json(PENNY_FILE, {})
    st = load_settings()
    return {"stocks": (data.get("stocks") or {}).get("result"), "crypto": (data.get("crypto") or {}).get("result"),
            "test": (data.get("test") or {}).get("result"), "settings": _penny_clean(st.get("penny")),
            "defaults": {"stocks": _penny_defaults("stocks"), "crypto": _penny_defaults("crypto")},
            "running": sorted(_penny_running), "error": data.get("error") or {}}


def _penny_defaults(kind: str) -> dict:
    return {k: v for k, v in asdict(_penny_rules(kind, {})).items() if k in PENNY_KEYS[kind]}


def _penny_due(t) -> list[str]:
    """Which automatic scans are due now: after the first manual scan, every evening for stocks and once a day for coins."""
    pen = _penny_clean(load_settings().get("penny"))
    if not pen["auto"]:
        return []
    data = _read_json(PENNY_FILE, {})
    done, today = data.get("auto", {}), t.date().isoformat()
    due = []
    if data.get("stocks") and _penny_after_close(t) and done.get("stocks") != today:
        due.append("stocks")
    if data.get("crypto") and t.hour >= 9 and done.get("crypto") != today:
        due.append("crypto")
    return [k for k in due if k not in _penny_running]


def run_penny_auto(kind: str) -> None:
    """The evening scan: run it with the saved choices and raise an alert when the list changed."""
    from . import alerts as AL

    _penny_running.add(kind)
    try:
        settings = load_settings()
        try:
            res = (run_penny_stocks if kind == "stocks" else run_penny_crypto)({}, settings, lambda m, f: None)
        except Exception as exc:
            traceback.print_exc()
            with _penny_file_lock:
                data = _read_json(PENNY_FILE, {})
                data.setdefault("error", {})[kind] = f"{type(exc).__name__}: {exc}"
                data.setdefault("auto", {})[kind] = date.today().isoformat()      # try again tomorrow, not every 5 minutes
                _write_json(PENNY_FILE, data)
            return
        with _penny_file_lock:
            data = _read_json(PENNY_FILE, {})
            data.setdefault("error", {}).pop(kind, None)
            _write_json(PENNY_FILE, data)
        ch = res.get("changes") or {}
        if not ch.get("first"):
            ev = AL.penny_change_event("Penny stocks" if kind == "stocks" else "Penny coins", ch.get("new", []), ch.get("out", []))
            if ev:
                store = AL.Store(ALERTS_FILE)
                with _alerts_lock:
                    d = store.load()
                    new = store.add(d, [ev])
                    d["problems"] = AL.deliver(new, d["config"]) if new else d.get("problems", [])
                    store.save(d)
    finally:
        _penny_running.discard(kind)


def run_longterm(settings: dict, progress) -> dict:
    from . import longterm

    return clean(longterm.run(float(settings["capital"]), int(settings.get("top") or 10), False, CACHE, progress))


def run_ipo(days: int, progress) -> dict:
    from . import ipo

    progress("Reading NSE's list of new listings", 0.05)
    df = ipo.listings(days, False, CACHE, progress=lambda m, f: progress(m, 0.1 + f * 1.2))
    progress("Reading IPO news", 0.85)
    news = ipo.ipo_news(False, CACHE)
    progress("Done", 1.0)
    return {"days": days, "listings": clean(df.to_dict("records")),
            "news": [{**clean(h), "label": h.label} for h in news]}


def run_stock(ticker: str, settings: dict, progress) -> dict:
    from . import options, sizing
    from .backtest import evaluate_rules, rank_rules
    from .data import load_prices
    from .news import load_news, summarize
    from .patterns import PATTERN_BY_NAME, detect_all
    from .report import build_outlook

    ticker = ticker.strip().upper()
    horizon = int(settings["horizon"])
    progress(f"Loading 10 years of {ticker} prices", 0.1)
    df = load_prices(ticker, "10y", "1d", False, CACHE)
    progress("Backtesting 22 candlestick patterns", 0.35)
    rules = evaluate_rules(df, horizons=sorted({1, 3, 5, 10, horizon}))
    progress("Reading news", 0.55)
    heads = load_news(ticker, None, False, CACHE)
    outlook = build_outlook(ticker, df, rules, heads, horizon=horizon)
    progress("Checking the option chain", 0.75)
    chain = options.load_chain(ticker, False, CACHE)
    trade = options.recommend(outlook, df, chain)
    plan = None
    if len(df) < 250:  # a new listing: nothing to backtest, so no verdict
        trade.action = "NO TRADE"
        trade.reason = (f"Listed only {len(df)} trading sessions ago. The candlestick rules need about a year of history "
                        "to be tested, so this page shows the chart and news only.")
        trade.warnings = []
    if trade.action != "NO TRADE":
        counted = [s for s in outlook.signals if s.get("counted")]
        sig = counted[0] if counted else None
        plan = sizing.plan_trade(ticker, df, outlook.bias, horizon, sig["pattern"] if sig else None,
                                 sig["date"] if sig else None, chain, float(settings["capital"]),
                                 float(settings["risk"]) / 100, cache_dir=CACHE)

    tail = df.tail(120)
    sig = detect_all(df).loc[tail.index]
    candles = [{"d": str(i.date()), "o": r.Open, "h": r.High, "l": r.Low, "c": r.Close, "v": r.Volume}
               for i, r in tail.iterrows()]
    markers = [{"d": str(i.date()), "name": n, "dir": PATTERN_BY_NAME[n].direction}
               for i, row in sig.iterrows() for n in row.index[row.to_numpy()]]
    ranked = rank_rules(rules, 10).head(12)
    progress("Done", 1.0)
    return {
        "ticker": ticker, "as_of": str(df.index[-1].date()), "last_close": float(df["Close"].iloc[-1]),
        "change": float(df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1),
        "outlook": {k: v for k, v in clean(outlook).items() if k != "signals"},
        "signals": clean(outlook.signals),
        "trade": {"action": trade.action, "reason": trade.reason,
                  # the order below already names an expiry, so drop the "pick one yourself" hint
                  "warnings": [w for w in trade.warnings if not (plan and w.startswith("no option chain"))],
                  "prob_profit": trade.prob_profit, "sample": trade.sample},
        "plan": plan_dict(plan), "candles": clean(candles), "markers": markers,
        "rules": clean(ranked[["pattern", "direction", "horizon", "n", "win_rate", "baseline", "edge",
                               "avg_return", "wilson_lb"]].to_dict("records")),
        "news": {"summary": clean(summarize(heads)),
                 "items": [{**clean(h), "label": h.label} for h in heads[:20]]},
    }


# --------------------------------------------------------------------------- journal
def _apply_kite(e: dict) -> None:
    """Close a journal entry once Kite reports that the whole position has been sold."""
    k = e.get("kite")
    if not k:
        return
    filled = int(k.get("filled_qty") or 0)
    if k.get("avg_price"):
        e["entry_premium"] = k["avg_price"]
    exited = sum(int(x["qty"]) for x in k.get("exits", []))
    if filled and exited >= filled and e.get("status") != "closed":
        value = sum(float(x["price"]) * int(x["qty"]) for x in k["exits"])
        e["exit_premium"] = round(value / exited, 2)
        e["pnl"] = round(value - float(k["avg_price"]) * exited, 2)
        e["status"], e["closed"] = "closed", date.today().isoformat()
    elif k.get("buy_status") in ("REJECTED", "CANCELLED"):
        e["status"], e["pnl"], e["closed"] = "cancelled", 0.0, date.today().isoformat()


def journal_summary(entries: list[dict]) -> dict:
    closed = [e for e in entries if e.get("status") == "closed" and e.get("pnl") is not None]
    wins = [e for e in closed if e["pnl"] > 0]
    return {"open": sum(1 for e in entries if e.get("status") == "open"), "closed": len(closed),
            "wins": len(wins), "win_rate": (len(wins) / len(closed)) if closed else None,
            "pnl": round(sum(e["pnl"] for e in closed), 2)}


def journal_op(method: str, path: str, body: dict) -> dict:
    with _lock:
        entries = _read_json(JOURNAL_FILE, [])
        parts = path.strip("/").split("/")
        if method == "POST" and len(parts) == 2:  # add
            e = {k: body.get(k) for k in ("ticker", "contract", "side", "lots", "lot_size", "entry_premium",
                                          "stop", "target1", "target2", "time_exit", "pattern", "notes", "kite")}
            e.update(id=uuid.uuid4().hex[:8], opened=date.today().isoformat(), status="open",
                     exit_premium=None, closed=None, pnl=None)
            entries.insert(0, e)
        elif len(parts) == 3:
            e = next((x for x in entries if x["id"] == parts[2]), None)
            if e is None:
                raise KeyError("trade not found")
            if method == "DELETE":
                entries.remove(e)
            else:  # close / edit
                for k in ("entry_premium", "lots", "notes", "exit_premium"):
                    if k in body:
                        e[k] = body[k]
                if body.get("exit_premium") not in (None, ""):
                    e["status"], e["closed"] = "closed", date.today().isoformat()
                    e["pnl"] = round((float(e["exit_premium"]) - float(e["entry_premium"] or 0))
                                     * float(e["lot_size"] or 0) * float(e["lots"] or 0), 2)
        for e in entries:
            _apply_kite(e)
        _write_json(JOURNAL_FILE, entries)
        return {"entries": entries, "summary": journal_summary(entries)}


def _norm_symbol(sym: str) -> str:
    sym = (sym or "").strip().upper()
    if not sym:
        raise ValueError("Enter a symbol")
    if "." not in sym and "-" not in sym and "=" not in sym and not sym.startswith("^"):
        sym += ".NS"                     # plain NSE symbols: TCS -> TCS.NS
    return sym


def holdings_op(method: str, path: str, body: dict) -> dict:
    """My holdings: long-term shares and ETFs with buy price and date (used by Portfolio risk and Tax)."""
    with _lock:
        rows = _read_json(HOLDINGS_FILE, [])
        parts = path.strip("/").split("/")
        if method == "POST" and len(parts) == 2:
            qty, price = float(body.get("qty") or 0), float(body.get("buy_price") or 0)
            if qty <= 0 or price <= 0:
                raise ValueError("Enter the quantity and the price you bought at")
            bd = str(body.get("buy_date") or date.today().isoformat())[:10]
            date.fromisoformat(bd)
            rows.append({"id": uuid.uuid4().hex[:8], "symbol": _norm_symbol(body.get("symbol")), "name": str(body.get("name") or "")[:60],
                         "qty": qty, "buy_price": price, "buy_date": bd,
                         "stop": float(body["stop"]) if body.get("stop") not in (None, "") else None, "note": str(body.get("note") or "")[:120]})
        elif len(parts) == 4 and parts[3] == "sell":          # record a sale (for tax), reduce the holding
            h = next((x for x in rows if x["id"] == parts[2]), None)
            if h is None:
                raise KeyError("holding not found")
            qty, price = float(body.get("qty") or 0), float(body.get("price") or 0)
            if not 0 < qty <= float(h["qty"]) or price <= 0:
                raise ValueError(f"Sell between 1 and {h['qty']:g} at a price above zero")
            d = str(body.get("date") or date.today().isoformat())[:10]
            date.fromisoformat(d)
            sales = _read_json(SALES_FILE, [])
            sales.append({"id": uuid.uuid4().hex[:8], "symbol": h["symbol"], "qty": qty, "price": price, "date": d,
                          "buy_price": h["buy_price"], "buy_date": h["buy_date"]})
            _write_json(SALES_FILE, sales)
            h["qty"] = float(h["qty"]) - qty
            if h["qty"] <= 1e-9:
                rows.remove(h)
        elif len(parts) == 3:
            h = next((x for x in rows if x["id"] == parts[2]), None)
            if h is None:
                raise KeyError("holding not found")
            if method == "DELETE":
                rows.remove(h)
            else:
                for k in ("qty", "buy_price", "stop"):
                    if k in body:
                        h[k] = float(body[k]) if body[k] not in (None, "") else None
                for k in ("buy_date", "name", "note"):
                    if k in body:
                        h[k] = str(body[k])
        _write_json(HOLDINGS_FILE, rows)
        return {"holdings": rows, "sales": _read_json(SALES_FILE, [])}


def _closes(symbols: list[str], progress=None) -> dict:
    """Ten years of daily closes for these symbols (from the cache, refreshed first)."""
    from .data import _cache_path, read_cached, refresh_many

    symbols = sorted(set(symbols))
    refresh_many(symbols, "10y", "1d", CACHE, progress=progress)
    out = {}
    for s_ in symbols:
        path = _cache_path(CACHE, s_, "10y", "1d")
        if path.exists():
            try:
                out[s_] = read_cached(path)["Close"]
            except Exception:
                continue
    return out


def run_tax(body: dict, settings: dict, progress) -> dict:
    from . import costs, tax

    holdings, sales = _read_json(HOLDINGS_FILE, []), _read_json(SALES_FILE, [])
    progress("Loading prices", 0.1)
    hist = _closes([h["symbol"] for h in holdings], progress=lambda m, f: progress(m, 0.1 + 0.7 * f))
    prices = {k: float(v.dropna().iloc[-1]) for k, v in hist.items() if len(v.dropna())}
    fy0, fy1 = tax.fy_bounds(date.today())
    fno = sum(float(e.get("pnl") or 0) for e in _read_json(JOURNAL_FILE, [])
              if e.get("status") == "closed" and e.get("closed") and fy0 <= date.fromisoformat(e["closed"]) <= fy1
              and not (e.get("kite") or {}).get("practice"))
    num = lambda k: float(body.get(k) or 0)
    sell = lambda v: costs.trade_cost(v, "sell", 1e9, 0.015)["charges"] + costs.trade_cost(v, "sell", 1e9, 0.015)["spread_impact"]
    out = tax.plan(holdings, prices, sales, num("extra_st"), num("extra_lt"), num("cf_st"), num("cf_lt"), fno, sell_cost=sell)
    progress("Done", 1.0)
    return clean({**out, "sales": sales})


# --------------------------------------------------------------------------- alerts
_alerts_lock = threading.Lock()


def _alert_items() -> list[dict]:
    """Every open position with levels worth watching."""
    from .paper import bearish

    items = []
    for e in _read_json(JOURNAL_FILE, []):
        if e.get("status") == "open" and e.get("ticker"):
            items.append({"key": f"j:{e['id']}", "label": e.get("contract") or e["ticker"], "symbol": e["ticker"],
                          "bearish": (e.get("side") or "").upper() == "PUT", "stop": e.get("stop"),
                          "target": e.get("target1"), "exit_by": e.get("time_exit"), "source": "My trades"})
    for h in _read_json(HOLDINGS_FILE, []):
        if h.get("stop"):
            items.append({"key": f"h:{h['id']}", "label": h["symbol"].replace(".NS", ""), "symbol": h["symbol"],
                          "bearish": False, "stop": h["stop"], "source": "Holdings"})
    for p in (_read_json(PAPER_FILE, {}) or {}).get("positions", []):
        if p.get("stop"):                      # paper closes itself at stop and target; warn only when near
            items.append({"key": f"p:{p['id']}", "label": p["symbol"].replace(".NS", ""), "symbol": p["symbol"],
                          "bearish": bearish(p), "stop": p["stop"], "source": "Paper"})
    return items


def _market_hours(now: datetime | None = None) -> bool:
    from .paper import now_ist

    t = now or now_ist()
    return t.weekday() < 5 and (9, 10) <= (t.hour, t.minute) <= (15, 40)


def run_alert_check(daily: bool = False, force: bool = False) -> list[dict]:
    """Look at everything once; store and send what is new. Called by the background loop and 'Check now'."""
    from . import alerts as AL
    from .paper import PRICES, PaperError

    store = AL.Store(ALERTS_FILE)
    with _alerts_lock:
        d = store.load()
    cfg = d["config"]
    if not cfg.get("enabled", True) and not force:
        return []
    events = []
    with _paper_lock:
        try:
            for ev in paper_account().process():
                events.append({"key": f"paper:{ev}", "kind": "paper", "level": "medium", "title": "Paper trading", "text": ev})
        except PaperError:
            pass
        except Exception:
            traceback.print_exc()
    price_of = lambda sym: PRICES.last(sym)[0]
    events += AL.position_events(_alert_items(), price_of, float(cfg.get("near_pct") or 1.0))
    rule_events, fired = AL.price_rule_events(d["rules"], price_of)
    events += rule_events
    if daily:
        if cfg.get("fund_rebalance"):
            ev = AL.rebalance_event(AL.next_period_end(date.today(), cfg["fund_rebalance"]))
            if ev:
                events.append(ev)
        watch = d.get("screen")
        if watch:
            try:
                res = run_myscreen({**watch["body"], "skip_backtest": True}, load_settings(), lambda m, f: None)
                now = [r["ticker"] for r in res["picks"]]
                ev = AL.screen_change_event(watch.get("picks") or [], now)
                if ev:
                    events.append(ev)
                watch["picks"] = now
            except Exception as exc:
                events.append({"key": f"screen-error:{date.today()}", "kind": "screen", "level": "low",
                               "title": "Could not re-run your watched screen", "text": str(exc)[:200]})
    with _alerts_lock:
        d2 = store.load()                      # the page may have changed settings meanwhile
        d2["rules"] = [r for r in d2["rules"] if r["id"] not in fired]
        if daily and d.get("screen") and d2.get("screen"):
            d2["screen"]["picks"] = d["screen"].get("picks")
        new = store.add(d2, events)
        d2["problems"] = AL.deliver(new, d2["config"]) if new else d2.get("problems", [])
        d2["last_check"] = datetime.now().isoformat(timespec="seconds")
        if daily:
            d2["daily_done"] = date.today().isoformat()
        store.save(d2)
    return new


def _alert_loop() -> None:
    from . import alerts as AL
    from .paper import now_ist

    while True:
        time.sleep(300)
        try:
            for kind in _penny_due(now_ist()):                  # heavy: in its own thread so stop alerts keep running
                threading.Thread(target=run_penny_auto, args=(kind,), daemon=True, name=f"penny-{kind}").start()
            d = AL.Store(ALERTS_FILE).load()
            if not d["config"].get("enabled", True):
                continue
            t = now_ist()
            crypto = any(p.get("instrument") == "crypto" for p in (_read_json(PAPER_FILE, {}) or {}).get("positions", []))
            if _market_hours(t) or crypto or d["rules"]:
                run_alert_check()
            if t.weekday() < 5 and (t.hour, t.minute) >= (15, 45) and d.get("daily_done") != t.date().isoformat():
                run_alert_check(daily=True)
        except Exception:
            traceback.print_exc()


def alerts_op(method: str, path: str, body: dict) -> dict:
    from . import alerts as AL

    store = AL.Store(ALERTS_FILE)
    parts = path.strip("/").split("/")          # api, alerts, ...
    with _alerts_lock:
        d = store.load()
        cfg = d["config"]
        what = parts[2] if len(parts) > 2 else ""
        if method == "POST" and what == "read":
            for it in d["items"]:
                it["read"] = True
        elif method == "POST" and what == "config":
            if "enabled" in body:
                cfg["enabled"] = bool(body["enabled"])
            if "near_pct" in body:
                cfg["near_pct"] = min(max(float(body["near_pct"] or 1), 0.2), 10.0)
            if "fund_rebalance" in body:
                cfg["fund_rebalance"] = body["fund_rebalance"] if body["fund_rebalance"] in ("M", "Q", "H", "Y") else None
            if "termux" in body:
                cfg["termux"] = bool(body["termux"])
            if body.get("telegram_token"):
                cfg["telegram_token"] = str(body["telegram_token"]).strip()
            if "telegram_chat" in body:
                cfg["telegram_chat"] = str(body["telegram_chat"] or "").strip() or None
            if body.get("telegram_forget"):
                cfg.pop("telegram_token", None)
                cfg.pop("telegram_chat", None)
        elif method == "POST" and what == "telegram-chat":
            if not cfg.get("telegram_token"):
                raise ValueError("Save the bot token first")
            chat = AL.telegram_chat_id(cfg["telegram_token"])
            if not chat:
                raise ValueError("No message found: open your bot in Telegram, press Start (or send it any message), then try again")
            cfg["telegram_chat"] = chat
        elif method == "POST" and what == "test":
            new = store.add(d, [{"key": f"test:{uuid.uuid4().hex[:6]}", "kind": "test", "level": "low",
                                 "title": "Test alert", "text": "Alerts are working. You will hear about stops, targets and your price alerts here."}])
            d["problems"] = AL.deliver(new, cfg)
        elif method == "POST" and what == "rule":
            price = float(body.get("price") or 0)
            if price <= 0 or body.get("op") not in ("above", "below"):
                raise ValueError("Choose above or below and a price")
            d["rules"].append({"id": uuid.uuid4().hex[:8], "symbol": _norm_symbol(body.get("symbol")), "op": body["op"],
                               "price": price, "note": str(body.get("note") or "")[:80]})
        elif method == "DELETE" and what == "rule" and len(parts) == 4:
            d["rules"] = [r for r in d["rules"] if r["id"] != parts[3]]
        elif method == "POST" and what == "unwatch-screen":
            d.pop("screen", None)
        store.save(d)
    return alerts_state()


def alerts_state() -> dict:
    from . import alerts as AL

    d = AL.Store(ALERTS_FILE).load()
    cfg = d["config"]
    return {"items": d["items"][:60], "unread": sum(1 for x in d["items"] if not x.get("read")), "rules": d["rules"],
            "config": {"enabled": cfg.get("enabled", True), "near_pct": cfg.get("near_pct", 1.0),
                       "fund_rebalance": cfg.get("fund_rebalance"), "termux": cfg.get("termux", True),
                       "telegram": bool(cfg.get("telegram_token")), "telegram_chat": cfg.get("telegram_chat")},
            "termux_available": AL.termux_available(), "problems": d.get("problems", []),
            "screen": ({"universe": d["screen"]["body"].get("universe"), "picks": d["screen"].get("picks", []),
                        "rules": len(d["screen"]["body"].get("filters") or [])} if d.get("screen") else None),
            "last_check": d.get("last_check")}


def home_summary() -> dict:
    """The dashboard: everything that needs a look today, from what is already on disk (no scans)."""
    from . import alerts as AL
    from . import tax

    out: dict = {}
    with _paper_lock:
        try:
            st = paper_account().state(refresh=False) if PAPER_FILE.exists() else None
        except Exception:
            st = None
    if st:
        pos = sorted(st["positions"], key=lambda x: x.get("pnl") or 0)
        out["paper"] = {"equity": st["equity"], "pnl": st["pnl"], "return_pct": st["return_pct"], "trades": st["trades"],
                        "win_rate": st["win_rate"], "positions": [{"label": x["label"], "pnl": x["pnl"], "stop": x.get("stop"),
                                                                    "spot": x.get("spot")} for x in pos]}
    journal = _read_json(JOURNAL_FILE, [])
    open_ = [e for e in journal if e.get("status") == "open"]
    out["journal"] = {"open": len(open_), "summary": journal_summary(journal),
                      "items": [{"label": e.get("contract") or e.get("ticker"), "stop": e.get("stop"), "exit_by": e.get("time_exit"),
                                 "live": bool(e.get("kite") and not e["kite"].get("practice"))} for e in open_[:6]]}
    holdings = _read_json(HOLDINGS_FILE, [])
    out["holdings"] = {"count": len(holdings), "cost": sum(float(h["qty"]) * float(h["buy_price"]) for h in holdings)}
    a = AL.Store(ALERTS_FILE).load()
    out["alerts"] = {"unread": sum(1 for x in a["items"] if not x.get("read")), "latest": a["items"][:3]}
    today = date.today()
    dates = []
    freq = a["config"].get("fund_rebalance") or "Q"
    dates.append({"date": AL.next_period_end(today, freq).isoformat(), "what": f"Model fund rebalance ({REBALANCE_WORD.get(freq, freq)})", "tab": "fund"})
    dates.append({"date": tax.fy_bounds(today)[1].isoformat(), "what": "Financial year ends: last day for tax harvesting", "tab": "tax"})
    for e in open_:
        if e.get("time_exit"):
            dates.append({"date": str(e["time_exit"])[:10], "what": f"Exit date: {e.get('contract') or e.get('ticker')}", "tab": "journal"})
    for x in (st or {}).get("positions", []):
        if x.get("exit_by"):
            dates.append({"date": str(x["exit_by"])[:10], "what": f"Paper exit date: {x['label']}", "tab": "paper"})
    pd_ = _read_json(PENNY_FILE, {})
    out["penny"] = {k: {"as_of": r.get("as_of"), "count": len(r.get("picks", [])), "top": [x["ticker"] for x in r.get("picks", [])[:3]],
                        "new": (r.get("changes") or {}).get("new", [])}
                    for k in ("stocks", "crypto") if (r := (pd_.get(k) or {}).get("result"))}
    out["dates"] = sorted((d for d in dates if d["date"] >= today.isoformat()), key=lambda d: d["date"])[:6]
    return out


REBALANCE_WORD = {"M": "monthly", "Q": "quarterly", "H": "half-yearly", "Y": "yearly"}


def run_risk(body: dict, settings: dict, progress) -> dict:
    from . import risk
    from .universes import industries

    include = set(body.get("include") or ["paper", "journal", "holdings"])
    progress("Collecting your positions", 0.05)
    acct = paper_account() if "paper" in include else None
    journal = _read_json(JOURNAL_FILE, []) if "journal" in include else []
    holdings = _read_json(HOLDINGS_FILE, []) if "holdings" in include else []
    syms = [p["symbol"] for p in (acct.data.get("positions", []) if acct else [])]
    syms += [e["ticker"] for e in journal if e.get("status") == "open" and e.get("ticker")]
    syms += [h["symbol"] for h in holdings]
    progress("Loading prices", 0.15)
    hist = _closes(syms + ["NIFTYBEES.NS", "USDINR=X"], progress=lambda m, f: progress(m, 0.15 + 0.6 * f))
    fx = float(hist["USDINR=X"].dropna().iloc[-1]) if "USDINR=X" in hist else 85.0

    def vol_of(sym):
        r = hist[sym].pct_change().dropna() if sym in hist else pd.Series(dtype=float)
        return max(float(r.iloc[-20:].std()), float(r.iloc[-60:].std())) * math.sqrt(252) * 1.1 if len(r) > 60 else 0.3
    positions = (risk.from_paper(acct) if acct else []) + risk.from_journal(journal, vol_of) + risk.from_holdings(holdings, fx)
    progress("Measuring the risk", 0.8)
    out = risk.analyze(positions, hist, hist.get("NIFTYBEES.NS", pd.Series(dtype=float)), float(settings["capital"]), industries(True))
    progress("Done", 1.0)
    return clean({**out, "include": sorted(include)})


def kite_place(body: dict) -> dict:
    from . import kite as K

    st = load_settings()
    # never trust a preview computed in the browser: rebuild it here from the plan
    b = kite_client()
    pv = K.preview(b, CACHE, body["ticker"], body["plan"], float(st["capital"]), float(st["risk"]) / 100,
                   float(body["limit"]) if body.get("limit") else None)
    rec = K.place(b, pv, confirmed=body.get("confirm") or [])
    rec["checklist"] = pv.get("checklist")
    plan = body["plan"]
    rec.setdefault("planned", {})["estimate"] = plan.get("premium")
    entry = {"ticker": body["ticker"], "contract": pv["tradingsymbol"], "side": pv["side"], "lots": pv["lots"],
             "lot_size": pv["lot_size"], "entry_premium": rec.get("avg_price") or pv["limit"],
             "stop": plan["stop_underlying"], "target1": plan["target1_underlying"], "target2": plan["target2_underlying"],
             "time_exit": plan.get("time_stop"), "pattern": body.get("pattern"),
             "notes": f"{b.label} {'practice' if rec['practice'] else 'live'}", "kite": rec}
    return journal_op("POST", "/api/journal", entry)


def kite_sync() -> dict:
    from . import kite as K

    with _lock:
        entries = _read_json(JOURNAL_FILE, [])
    errors = []
    for e in entries:
        if e.get("kite") and e.get("status") == "open" and not e["kite"].get("practice"):
            try:
                K.sync_one(broker_client(e["kite"].get("broker") or "kite"), e["kite"])
            except K.KiteError as exc:
                errors.append(f"{e['contract']}: {exc}")
            _apply_kite(e)
    with _lock:
        _write_json(JOURNAL_FILE, entries)
    return {"entries": entries, "summary": journal_summary(entries), "errors": errors}


def kite_exit(trade_id: str, price) -> dict:
    from . import kite as K

    with _lock:
        entries = _read_json(JOURNAL_FILE, [])
    e = next((x for x in entries if x["id"] == trade_id), None)
    if not e or not e.get("kite"):
        raise KeyError("Kite trade not found")
    K.exit_now(broker_client(e["kite"].get("broker") or "kite"), e["kite"], float(price) if price not in (None, "") else None)
    _apply_kite(e)
    with _lock:
        _write_json(JOURNAL_FILE, entries)
    return {"entries": entries, "summary": journal_summary(entries)}


# --------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "StockAgent/1"

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def _send(self, code: int, payload, ctype="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _local_client(self) -> bool:
        """This computer's own browser: a loopback connection that also addresses the server as localhost.

        Requests relayed by the tunnel also arrive over loopback, but carry the tunnel's host name, so they
        are treated as remote and must present the key.
        """
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1") and host in ("127.0.0.1", "localhost")

    def _host_ok(self) -> bool:
        """Only plain addresses are accepted as Host, which blocks DNS-rebinding attacks."""
        import re

        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host in ("127.0.0.1", "localhost"):
            return True
        if PHONE.get("tunnel_host") and host == PHONE["tunnel_host"]:
            return True
        return PHONE["on"] and not PHONE.get("tunnel_only") and bool(re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host))

    def _key_ok(self) -> bool:
        """Devices other than this computer need the phone key (in the cookie, after the first visit)."""
        if self._local_client() and not PHONE.get("lock_local"):
            return True
        if not PHONE["on"]:
            return False
        from http.cookies import SimpleCookie

        c = SimpleCookie(self.headers.get("Cookie") or "")
        return "agent_key" in c and secrets.compare_digest(c["agent_key"].value, PHONE["key"])

    def _token_ok(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Agent-Token") or "", SESSION_TOKEN)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if not self._host_ok():
            return self._send(403, {"error": "forbidden host"})
        # app icon and manifest: harmless, and needed by "Add to Home screen" before the key cookie exists
        if url.path == "/manifest.webmanifest":
            return self._send(200, json.dumps(MANIFEST).encode(), "application/manifest+json")
        if url.path in ("/icon-192.png", "/icon-512.png", "/apple-touch-icon.png", "/favicon.ico"):
            size = 512 if "512" in url.path else 192
            if size not in _ICONS:
                _ICONS[size] = _icon_png(size)
            return self._send(200, _ICONS[size], "image/png")
        if not self._key_ok():
            if PHONE["on"] and url.path in ("/", "/index.html") and q.get("key"):
                if secrets.compare_digest(q["key"], PHONE["key"]):
                    secure = "; Secure" if PHONE.get("tunnel_host") and self.headers.get("Host", "").startswith(PHONE["tunnel_host"]) else ""
                    self.send_response(303)
                    self.send_header("Set-Cookie", f"agent_key={PHONE['key']}; Max-Age=31536000; Path=/; HttpOnly; SameSite=Strict{secure}")
                    self.send_header("Location", "/")
                    self.end_headers()
                    return
            if q.get("key"):
                time.sleep(1.0)  # a wrong key: slow down anyone guessing
            page = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                    "<body style='font:16px system-ui;padding:32px'><h2>Stock Agent</h2><p>Open the phone link shown "
                    "in the Stock Agent window on your computer (it ends with <b>?key=…</b>).</p>")
            return self._send(403, page.encode("utf-8"), "text/html; charset=utf-8")
        try:
            if url.path == "/api/phone-qr.svg":
                if not self._token_ok():
                    return self._send(403, {"error": "missing app token; reload the page"})
                link = PHONE.get("tunnel_url") or (PHONE.get("urls") or [None])[0]
                if not link:
                    return self._send(404, {"error": "phone mode is off"})
                return self._send(200, qr_svg(link), "image/svg+xml")
            if url.path == "/api/phone":
                if not self._token_ok():
                    return self._send(403, {"error": "missing app token; reload the page"})
                return self._send(200, {"on": PHONE["on"], "urls": PHONE["urls"], "tunnel_url": PHONE.get("tunnel_url"),
                                        "tunnel_only": bool(PHONE.get("tunnel_only")), "android_app": bool(os.environ.get("STOCK_AGENT_ANDROID"))})
            if url.path == "/api/selftest":
                if not self._token_ok():
                    return self._send(403, {"error": "missing app token; reload the page"})
                from . import selftest

                return self._send(200, clean(selftest.run()))
            if url.path in ("/", "/index.html"):
                html = (WEB / "index.html").read_text(encoding="utf-8").replace("__AGENT_TOKEN__", SESSION_TOKEN)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if url.path == "/kite/callback":
                from .kite import KiteError

                try:
                    if q.get("status") != "success" or not q.get("request_token"):
                        raise KiteError("Kite login was cancelled")
                    st = broker_client("kite").complete_login(q["request_token"])
                    msg = f"Connected to Kite as {st.get('user') or 'you'}. You can close this tab and go back to the app."
                except Exception as exc:
                    msg = f"Kite login failed: {exc}"
                page = (f"<!doctype html><meta charset=utf-8><title>Kite login</title><body style='font:16px system-ui;"
                        f"padding:40px'><p>{msg.replace('<', '&lt;')}</p><p><a href='/'>Back to Stock Agent</a></p>")
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            if url.path.startswith("/broker/callback/"):
                from . import brokers

                key = url.path.rsplit("/", 1)[-1]
                label = brokers.LABELS.get(key, "Broker")
                try:
                    b = broker_client(key) if key in brokers.BROKERS else None
                    if not b or b.key != key:
                        raise brokers.BrokerError("unknown broker")
                    want = b.cfg.get("login_state")
                    got = q.get("state") or q.get("State")
                    if want and got != want:
                        raise brokers.BrokerError("the login did not start from this app; press Log in again")
                    token = q.get(b.LOGIN_PARAM)
                    if not token:
                        raise brokers.BrokerError("login was cancelled")
                    st = b.complete_login(token)
                    msg = f"Connected to {label} as {st.get('user') or 'you'}. You can close this tab and go back to the app."
                except Exception as exc:
                    msg = f"{label} login failed: {exc}"
                page = (f"<!doctype html><meta charset=utf-8><title>{label} login</title><body style='font:16px system-ui;"
                        f"padding:40px'><p>{msg.replace('<', '&lt;')}</p><p><a href='/'>Back to Stock Agent</a></p>")
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            if url.path.startswith("/api/") and not self._token_ok():
                return self._send(403, {"error": "missing app token; reload the page"})
            if url.path == "/api/kite/status":
                from . import brokers

                b = kite_client()
                st = b.status()
                port = self.server.server_address[1]
                st["brokers"] = brokers.LABELS
                st["redirect_url"] = broker_redirect(b.key, port)
                st["setup"] = [x.replace("{redirect}", st["redirect_url"]) for x in st.get("setup", [])]
                return self._send(200, st)
            if url.path == "/api/kite/login":
                from .broker_base import BrokerError

                b = kite_client()
                try:
                    if b.LOGIN_KIND == "direct":
                        return self._send(200, {"status": b.direct_login()})
                    if b.key != "kite":
                        b.cfg["login_state"] = secrets.token_urlsafe(12)
                        b.save()
                    url_ = b.login_url(broker_redirect(b.key, self.server.server_address[1]))
                    if b.key != "kite":
                        url_ += f"&{'State' if b.key == 'fivepaisa' else 'state'}={b.cfg['login_state']}"
                    return self._send(200, {"url": url_})
                except BrokerError as exc:
                    return self._send(400, {"error": str(exc)})
            if url.path == "/api/settings":
                return self._send(200, load_settings())
            if url.path == "/api/last-screen":
                return self._send(200, _read_json(HOME / "last_screen.json", None))
            if url.path.startswith("/api/job/"):
                job = _jobs.get(url.path.rsplit("/", 1)[-1])
                if not job:
                    return self._send(404, {"error": "unknown job"})
                if job["status"] == "done" and job["kind"] == "screen":
                    _write_json(HOME / "last_screen.json", job["result"])
                return self._send(200, job)
            if url.path == "/api/journal":
                entries = _read_json(JOURNAL_FILE, [])
                return self._send(200, {"entries": entries, "summary": journal_summary(entries)})
            if url.path == "/api/holdings":
                return self._send(200, {"holdings": _read_json(HOLDINGS_FILE, []), "sales": _read_json(SALES_FILE, [])})
            if url.path == "/api/alerts":
                return self._send(200, alerts_state())
            if url.path == "/api/penny":
                return self._send(200, clean(penny_state()))
            if url.path == "/api/home":
                return self._send(200, clean(home_summary()))
            if url.path == "/api/orders":
                from . import orders

                return self._send(200, clean(orders.build(_read_json(JOURNAL_FILE, []), _read_json(PAPER_FILE, None))))
            if url.path == "/api/symbols":
                from .universes import get_universe

                syms = set(get_universe("nse_all", True)) | set(get_universe("sp500", True))
                return self._send(200, sorted(syms))
            if url.path == "/api/track":
                return self._send(200, {"job": start_job("track", run_track, load_settings(), int(q.get("replay", 0) or 0))})
            if url.path == "/api/markets":
                market = q.get("market", "crypto")
                if market not in ("crypto", "commodities"):
                    return self._send(400, {"error": "unknown market"})
                return self._send(200, {"job": start_job("market", run_market, market, load_settings())})
            if url.path == "/api/paper":
                if q.get("refresh") == "0":
                    with _paper_lock:
                        return self._send(200, clean(paper_account().state(refresh=False)))
                return self._send(200, {"job": start_job("paper", run_paper)})
            if url.path == "/api/goal":
                return self._send(200, _read_json(GOAL_FILE, None))
            if url.path == "/api/portfolio":
                return self._send(200, _read_json(PORTFOLIO_FILE, None))
            if url.path == "/api/robust":
                if q.get("last"):
                    return self._send(200, _read_json(ROBUST_FILE, None))
                return self._send(200, {"job": start_job("robust", run_robust, load_settings())})
            if url.path == "/api/longterm":
                return self._send(200, {"job": start_job("longterm", run_longterm, load_settings())})
            if url.path == "/api/ipo":
                return self._send(200, {"job": start_job("ipo", run_ipo, max(7, min(int(q.get("days", 90)), 730)))})
            if url.path == "/api/stock":
                return self._send(200, {"job": start_job("stock", run_stock, q.get("ticker", ""), load_settings())})
            return self._send(404, {"error": "not found"})
        except Exception as exc:
            traceback.print_exc()
            return self._send(500, {"error": str(exc)})

    def do_POST(self):
        url = urlparse(self.path)
        if not (self._host_ok() and self._key_ok() and self._token_ok()):
            return self._send(403, {"error": "missing app token; reload the page"})
        try:
            body = self._body()
            if url.path.startswith("/api/kite/"):
                from . import kite as K

                try:
                    if url.path == "/api/kite/select":
                        from . import brokers

                        if body.get("broker") not in brokers.BROKERS:
                            return self._send(400, {"error": "unknown broker"})
                        s = load_settings()
                        s["broker"] = body["broker"]
                        _write_json(SETTINGS_FILE, s)
                        return self._send(200, kite_client().status())
                    if url.path == "/api/kite/config":
                        b = kite_client()
                        if "fields" in body:
                            return self._send(200, b.update(body.get("fields") or {}, body.get("practice")))
                        return self._send(200, b.update({"api_key": body.get("api_key"), "api_secret": body.get("api_secret") or ""},
                                                        body.get("practice")))
                    if url.path == "/api/kite/logout":
                        return self._send(200, kite_client().logout())
                    if url.path == "/api/kite/preview":
                        st = load_settings()
                        return self._send(200, K.preview(kite_client(), CACHE, body["ticker"], body["plan"],
                                                         float(st["capital"]), float(st["risk"]) / 100,
                                                         float(body["limit"]) if body.get("limit") else None))
                    if url.path == "/api/kite/place":
                        return self._send(200, kite_place(body))
                    if url.path == "/api/kite/sync":
                        return self._send(200, kite_sync())
                    if url.path.startswith("/api/kite/exit/"):
                        return self._send(200, kite_exit(url.path.rsplit("/", 1)[-1], body.get("price")))
                except K.KiteError as exc:
                    return self._send(400, {"error": str(exc)})
            if url.path == "/api/settings":
                s = load_settings()
                for k, v in body.items():
                    if k in DEFAULT_SETTINGS:
                        s[k] = v
                s["capital"] = max(float(s["capital"]), 1000.0)
                s["risk"] = min(max(float(s["risk"]), 0.1), 20.0)
                s["horizon"] = int(s["horizon"]) if int(s["horizon"]) in (3, 5, 10) else 5
                s["top"] = min(max(int(s["top"]), 1), 20)
                s["brokerage"] = min(max(float(s.get("brokerage") or 0), 0.0), 100.0)
                s["penny"] = _penny_clean(s.get("penny"))
                _write_json(SETTINGS_FILE, s)
                return self._send(200, s)
            if url.path == "/api/screen":
                return self._send(200, {"job": start_job("screen", run_screen, {**load_settings(), **body})})
            if url.path == "/api/goal":
                return self._send(200, {"job": start_job("goal", run_goal, body)})
            if url.path == "/api/myscreen":
                return self._send(200, {"job": start_job("myscreen", run_myscreen, body, load_settings())})
            if url.path == "/api/check":
                return self._send(200, {"job": start_job("check", run_check, body)})
            if url.path == "/api/fund":
                return self._send(200, {"job": start_job("fund", run_fund, body, load_settings())})
            if url.path == "/api/rebalance":
                return self._send(200, {"job": start_job("rebalance", run_rebalance, body)})
            if url.path.startswith("/api/journal"):
                return self._send(200, journal_op("POST", url.path, body))
            if url.path.startswith("/api/holdings"):
                try:
                    return self._send(200, holdings_op("POST", url.path, body))
                except (ValueError, KeyError) as exc:
                    return self._send(400, {"error": str(exc).strip("'")})
            if url.path in ("/api/penny/stocks", "/api/penny/crypto", "/api/penny/test"):
                kind = "crypto" if url.path.endswith("crypto") else "stocks"
                try:
                    _penny_remember(kind, body)
                except (ValueError, TypeError) as exc:
                    return self._send(400, {"error": str(exc)})
                fn = {"stocks": run_penny_stocks, "crypto": run_penny_crypto}.get(url.path.rsplit("/", 1)[-1], run_penny_test)
                return self._send(200, {"job": start_job("penny", fn, body, load_settings())})
            if url.path == "/api/penny/auto":
                s = _read_json(SETTINGS_FILE, {})
                s["penny"] = _penny_clean({**(s.get("penny") or {}), "auto": bool(body.get("on"))})
                _write_json(SETTINGS_FILE, s)
                return self._send(200, clean(penny_state()))
            if url.path == "/api/alerts/check":
                new = run_alert_check(force=True)
                return self._send(200, {**alerts_state(), "new": new})
            if url.path.startswith("/api/alerts/"):
                try:
                    return self._send(200, alerts_op("POST", url.path, body))
                except (ValueError, RuntimeError) as exc:
                    return self._send(400, {"error": str(exc)})
            if url.path == "/api/tax":
                return self._send(200, {"job": start_job("tax", run_tax, body, load_settings())})
            if url.path == "/api/risk":
                return self._send(200, {"job": start_job("risk", run_risk, body, load_settings())})
            if url.path.startswith("/api/paper/"):
                from .paper import PaperError

                try:
                    return self._send(200, clean(paper_op(url.path, body)))
                except PaperError as exc:
                    return self._send(400, {"error": str(exc)})
                except KeyError:
                    return self._send(404, {"error": "not found"})
            return self._send(404, {"error": "not found"})
        except Exception as exc:
            traceback.print_exc()
            return self._send(500, {"error": str(exc)})

    def do_DELETE(self):
        if not (self._host_ok() and self._key_ok() and self._token_ok()):
            return self._send(403, {"error": "missing app token; reload the page"})
        try:
            path = urlparse(self.path).path
            if path.startswith("/api/holdings/"):
                return self._send(200, holdings_op("DELETE", path, {}))
            if path.startswith("/api/alerts/"):
                return self._send(200, alerts_op("DELETE", path, {}))
            return self._send(200, journal_op("DELETE", path, {}))
        except Exception as exc:
            return self._send(500, {"error": str(exc)})


def find_cloudflared() -> str | None:
    """cloudflared on PATH, or where winget / the Windows installer put it."""
    import shutil

    exe = shutil.which("cloudflared")
    if exe:
        return exe
    candidates = [Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe",
                  Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "cloudflared" / "cloudflared.exe",
                  Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "cloudflared" / "cloudflared.exe"]
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if winget.is_dir():
        candidates += list(winget.glob("Cloudflare.cloudflared*/**/cloudflared*.exe"))
    return next((str(c) for c in candidates if c and Path(c).is_file()), None)


def start_tunnel(port: int, exe: str | None = None, timeout: float = 60.0) -> str:
    """Start a Cloudflare quick tunnel to this app and return its https address.

    The tunnel makes an outgoing connection to Cloudflare, so no firewall or router change is needed.
    """
    import atexit
    import re
    import subprocess

    exe = exe or find_cloudflared()
    if not exe:
        raise SystemExit("cloudflared is not installed. Install it once with:\n    winget install --id Cloudflare.cloudflared\n"
                         "then close this window and start the app again.")
    proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    atexit.register(lambda: proc.poll() is None and proc.terminate())
    found: dict = {}

    def read():
        for line in proc.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and "url" not in found:
                found["url"] = m.group(0)

    threading.Thread(target=read, daemon=True).start()
    end = time.time() + timeout
    while time.time() < end and "url" not in found:
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    if "url" not in found:
        proc.terminate()
        raise SystemExit("The Cloudflare tunnel did not start (no internet, or cloudflared blocked by antivirus). "
                         "Try again, or use the Wi-Fi link instead.")
    return found["url"]


def on_termux() -> bool:
    """Running inside Termux on an Android phone."""
    return "com.termux" in os.environ.get("PREFIX", "") or bool(os.environ.get("TERMUX_VERSION"))


def open_url(url: str) -> None:
    import shutil
    import subprocess

    if on_termux() and shutil.which("termux-open-url"):   # Android: hand the link to Chrome
        subprocess.run(["termux-open-url", url], check=False)
    else:
        webbrowser.open(url)


def serve(port: int = 8765, open_browser: bool = True, phone: bool = False, tunnel: bool = False,
          cloudflared: str | None = None) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    bind = "0.0.0.0" if phone and not tunnel else "127.0.0.1"   # the tunnel connects locally: no Wi-Fi exposure
    for p in range(port, port + 20):  # find a free port
        try:
            httpd = ThreadingHTTPServer((bind, p), Handler)
            break
        except OSError:
            continue
    else:
        raise SystemExit(f"No free port between {port} and {port + 19}")
    real_port = httpd.server_address[1]
    global LISTEN_PORT
    LISTEN_PORT = real_port                 # the Android app reads this to know where to point its window
    url = f"http://127.0.0.1:{real_port}/"
    print(f"Stock Agent is running at {url}")
    if tunnel:
        PHONE.update(on=True, key=_phone_key(min_len=16), tunnel_only=True)
        print("Starting a secure link through Cloudflare (takes up to a minute) ...")
        t_url = start_tunnel(real_port, cloudflared)
        PHONE["tunnel_host"] = t_url.split("//", 1)[1]
        PHONE["tunnel_url"] = f"{t_url}/?key={PHONE['key']}"
        print("\nOn your phone (Wi-Fi or mobile data), open in Chrome, or scan the QR code in Settings:")
        print(f"    {PHONE['tunnel_url']}")
        print("This link changes every time you start the app. Anyone with the full link can open the app,")
        print("so do not share it. Then use Chrome's menu > Add to Home screen.\n")
    elif phone:
        PHONE.update(on=True, key=_phone_key())
        PHONE["urls"] = [f"http://{ip}:{real_port}/?key={PHONE['key']}" for ip in _lan_ips()]
        print("\nOn your phone (connected to the same Wi-Fi), open in Chrome:")
        for u in PHONE["urls"] or ["(no Wi-Fi address found: is this computer on a network?)"]:
            print(f"    {u}")
        print("Then use Chrome's menu > Add to Home screen for an app icon.")
        print("If Windows asks about the firewall, allow Python on Private networks.\n")
    print("Keep this window open while you use the app. Close it (or press Ctrl+C) to stop.")
    if open_browser:
        threading.Timer(0.8, lambda: open_url(url)).start()
    threading.Thread(target=_alert_loop, daemon=True, name="alerts").start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


def make_shortcut(phone: bool = False, tunnel: bool = False) -> Path:
    """Put a double-click launcher on the desktop."""
    home = Path.home()
    desktops = [home / "OneDrive" / "Desktop", home / "Desktop", home]
    desktop = next(d for d in desktops if d.is_dir())
    py = Path(sys.executable)
    frozen = getattr(sys, "frozen", False)       # running as StockAgent.exe: the program itself is the launcher
    if os.name == "nt":
        pyw = py.with_name("python.exe")
        target = desktop / ("Stock Agent (anywhere).bat" if tunnel else "Stock Agent (phone).bat" if phone else "Stock Agent.bat")
        flag = " --tunnel" if tunnel else " --phone" if phone else ""
        run = f'"{py}" app{flag}' if frozen else f'"{pyw}" -m stock_agent app{flag}'
        target.write_text(f'@echo off\r\ntitle Stock Agent\r\n{run}\r\npause\r\n', encoding="utf-8")
    else:
        target = desktop / ("stock-agent-anywhere.command" if tunnel else "stock-agent-phone.command" if phone else "stock-agent.command")
        flag = " --tunnel" if tunnel else " --phone" if phone else ""
        target.write_text(f'#!/bin/sh\n"{py}" -m stock_agent app{flag}\n', encoding="utf-8")
        target.chmod(0o755)
    return target
