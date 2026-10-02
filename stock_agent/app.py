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
WEB = Path(__file__).with_name("web")

DEFAULT_SETTINGS = {
    "capital": 200000, "risk": 2.0, "universe": "nifty50", "custom": "", "horizon": 5,
    "affordable_only": False, "top": 5,
}

_lock = threading.Lock()
_jobs: dict[str, dict] = {}
# Per-run secret embedded in the page. Every request that changes something must carry it, so another
# website open in the same browser cannot drive this local server (and never your Kite account).
SESSION_TOKEN = secrets.token_urlsafe(24)
_kite = None


def kite_client():
    global _kite
    if _kite is None:
        from .kite import Kite

        _kite = Kite(HOME)
    return _kite


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
    from .universes import UNIVERSES

    if settings.get("universe") == "custom":
        tickers = [t.strip().upper() for t in str(settings.get("custom", "")).replace(";", ",").split(",") if t.strip()]
        if not tickers:
            raise ValueError("Add some symbols to your custom list in Settings, e.g. INFY.NS, TCS.NS")
    else:
        tickers = UNIVERSES.get(settings.get("universe", "nifty50"), UNIVERSES["nifty50"])
    picks, ranked, stats = screener.screen(
        tickers, horizon=int(settings["horizon"]), top=int(settings["top"]), cache_dir=CACHE,
        capital=float(settings["capital"]), risk_pct=float(settings["risk"]) / 100,
        affordable_only=bool(settings.get("affordable_only")), log=lambda *a: None, progress=progress,
    )
    out = []
    for p in picks:
        d = clean(p)
        d.pop("plan", None)
        d.update(side=p.side, edge=p.edge, plan=plan_dict(p.plan))
        out.append(d)
    return {"picks": out, "stats": stats, "settings": settings, "generated": datetime.now().isoformat(timespec="minutes")}


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
    pv = K.preview(kite_client(), CACHE, body["ticker"], body["plan"], float(st["capital"]), float(st["risk"]) / 100,
                   float(body["limit"]) if body.get("limit") else None)
    rec = K.place(kite_client(), pv)
    plan = body["plan"]
    entry = {"ticker": body["ticker"], "contract": pv["tradingsymbol"], "side": pv["side"], "lots": pv["lots"],
             "lot_size": pv["lot_size"], "entry_premium": rec.get("avg_price") or pv["limit"],
             "stop": plan["stop_underlying"], "target1": plan["target1_underlying"], "target2": plan["target2_underlying"],
             "time_exit": plan.get("time_stop"), "pattern": body.get("pattern"),
             "notes": "Kite practice" if rec["practice"] else "Kite live", "kite": rec}
    return journal_op("POST", "/api/journal", entry)


def kite_sync() -> dict:
    from . import kite as K

    with _lock:
        entries = _read_json(JOURNAL_FILE, [])
    errors = []
    for e in entries:
        if e.get("kite") and e.get("status") == "open" and not e["kite"].get("practice"):
            try:
                K.sync_one(kite_client(), e["kite"])
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
    K.exit_now(kite_client(), e["kite"], float(price) if price not in (None, "") else None)
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

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")  # blocks DNS-rebinding attacks

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
        try:
            if url.path in ("/", "/index.html"):
                html = (WEB / "index.html").read_text(encoding="utf-8").replace("__AGENT_TOKEN__", SESSION_TOKEN)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if url.path == "/kite/callback":
                from .kite import KiteError

                try:
                    if q.get("status") != "success" or not q.get("request_token"):
                        raise KiteError("Kite login was cancelled")
                    st = kite_client().complete_login(q["request_token"])
                    msg = f"Connected to Kite as {st.get('user') or 'you'}. You can close this tab and go back to the app."
                except Exception as exc:
                    msg = f"Kite login failed: {exc}"
                page = (f"<!doctype html><meta charset=utf-8><title>Kite login</title><body style='font:16px system-ui;"
                        f"padding:40px'><p>{msg.replace('<', '&lt;')}</p><p><a href='/'>Back to Stock Agent</a></p>")
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            if url.path.startswith("/api/") and not self._token_ok():
                return self._send(403, {"error": "missing app token; reload the page"})
            if url.path == "/api/kite/status":
                st = kite_client().status()
                st["redirect_url"] = f"http://127.0.0.1:{self.server.server_address[1]}/kite/callback"
                return self._send(200, st)
            if url.path == "/api/kite/login":
                return self._send(200, {"url": kite_client().login_url()})
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
                from .universes import UNIVERSES

                return self._send(200, sorted({t for v in UNIVERSES.values() for t in v}))
            if url.path == "/api/stock":
                return self._send(200, {"job": start_job("stock", run_stock, q.get("ticker", ""), load_settings())})
            return self._send(404, {"error": "not found"})
        except Exception as exc:
            traceback.print_exc()
            return self._send(500, {"error": str(exc)})

    def do_POST(self):
        url = urlparse(self.path)
        if not (self._host_ok() and self._token_ok()):
            return self._send(403, {"error": "missing app token; reload the page"})
        try:
            body = self._body()
            if url.path.startswith("/api/kite/"):
                from . import kite as K

                try:
                    if url.path == "/api/kite/config":
                        return self._send(200, kite_client().update_config(body.get("api_key"), body.get("api_secret"),
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
                _write_json(SETTINGS_FILE, s)
                return self._send(200, s)
            if url.path == "/api/screen":
                return self._send(200, {"job": start_job("screen", run_screen, {**load_settings(), **body})})
            if url.path.startswith("/api/journal"):
                return self._send(200, journal_op("POST", url.path, body))
            return self._send(404, {"error": "not found"})
        except Exception as exc:
            traceback.print_exc()
            return self._send(500, {"error": str(exc)})

    def do_DELETE(self):
        if not (self._host_ok() and self._token_ok()):
            return self._send(403, {"error": "missing app token; reload the page"})
        try:
            return self._send(200, journal_op("DELETE", urlparse(self.path).path, {}))
        except Exception as exc:
            return self._send(500, {"error": str(exc)})


def serve(port: int = 8765, open_browser: bool = True) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    for p in range(port, port + 20):  # find a free port
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            break
        except OSError:
            continue
    else:
        raise SystemExit(f"No free port between {port} and {port + 19}")
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"Stock Agent is running at {url}")
    print("Keep this window open while you use the app. Close it (or press Ctrl+C) to stop.")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


def make_shortcut() -> Path:
    """Put a double-click launcher on the desktop."""
    home = Path.home()
    desktops = [home / "OneDrive" / "Desktop", home / "Desktop", home]
    desktop = next(d for d in desktops if d.is_dir())
    py = Path(sys.executable)
    if os.name == "nt":
        pyw = py.with_name("python.exe")
        target = desktop / "Stock Agent.bat"
        target.write_text(f'@echo off\r\ntitle Stock Agent\r\n"{pyw}" -m stock_agent app\r\npause\r\n', encoding="utf-8")
    else:
        target = desktop / "stock-agent.command"
        target.write_text(f'#!/bin/sh\n"{py}" -m stock_agent app\n', encoding="utf-8")
        target.chmod(0o755)
    return target
