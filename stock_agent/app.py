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
                       fund_data=fund_data, cache_dir=CACHE)
    return clean({**res, "universe": uni})


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
        if self._local_client():
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
                                        "tunnel_only": bool(PHONE.get("tunnel_only"))})
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
            return self._send(200, journal_op("DELETE", urlparse(self.path).path, {}))
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
