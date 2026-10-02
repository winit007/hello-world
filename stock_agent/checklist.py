"""The six-point trade checklist. Take a trade only if every answer is yes.

1. Proven edge     this setup made money on data it was not tuned on, after costs
2. Planned exit    stop-loss, target and time limit are known before entering
3. Small risk      the loss at the stop is within your risk limit (2% of capital by default)
4. Liquid contract the bid-ask spread is under about 2% of the price
5. No big events   no company results (or RBI / budget day) before the planned exit
6. Intraday timing not in the first 15 minutes of the session, and out before the close

Each item comes back as "pass", "fail", "check" (only you can answer it: shown as a tick-box) or "na"
(does not apply, e.g. timing for a trade held for days). The app answers what it can automatically:
the strategy check and the setup's record, the order plan, your risk limit, the live spread when Kite
is connected, the next results date from Yahoo Finance, and the clock.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
TITLES = {
    "edge": "Proven edge",
    "exit": "Planned exit",
    "risk": "Small risk",
    "liquid": "Liquid contract",
    "events": "No big events",
    "timing": "Intraday timing",
}
QUESTIONS = {
    "edge": "Has this exact setup made money on data it wasn't tuned on, after costs?",
    "exit": "Do you know your stop-loss, target and time limit before entering?",
    "risk": "Is the loss at your stop within your risk limit?",
    "liquid": "Is the bid-ask spread under about 2% of the price?",
    "events": "No results, RBI decision or budget before your exit?",
    "timing": "Not in the first 15 minutes, and out before the close?",
}
SESSIONS = {  # (open, avoid until, must be out by)
    "nse": (dtime(9, 15), dtime(9, 30), dtime(15, 20)),
    "mcx": (dtime(9, 0), dtime(9, 15), dtime(23, 15)),
}


def _item(key, status, detail):
    return {"id": key, "title": TITLES[key], "question": QUESTIONS[key], "status": status, "detail": detail}


def next_results_date(ticker: str, cache_dir: Path) -> date | None:
    """Next scheduled results date from Yahoo Finance, cached for the day. None if unknown."""
    path = Path(cache_dir) / "events.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    today = date.today().isoformat()
    hit = data.get(ticker)
    if hit and hit.get("checked") == today:
        return date.fromisoformat(hit["date"]) if hit.get("date") else None
    found = None
    try:
        import logging

        import yfinance as yf

        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        cal = yf.Ticker(ticker).calendar or {}
        dates = [d for d in (cal.get("Earnings Date") or []) if isinstance(d, date)]
        future = [d for d in dates if d >= date.today()]
        found = min(future) if future else None
    except Exception:
        found = None
    data[ticker] = {"checked": today, "date": found.isoformat() if found else None}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass
    return found


def evaluate(*, edge_ok: bool | None, edge_detail: str, stop, target, time_exit, max_loss: float | None,
             budget: float | None, affordable: bool, spread_pct: float | None = None, results_date: date | None = None,
             results_known: bool = False, exit_date: date | None = None, intraday: bool = False,
             session: str = "nse", now: datetime | None = None, event_note: str = "RBI policy or budget day") -> dict:
    items = []
    # 1 proven edge
    if edge_ok is None:
        items.append(_item("edge", "check", edge_detail))
    else:
        items.append(_item("edge", "pass" if edge_ok else "fail", edge_detail))
    # 2 planned exit
    has_exit = all(v not in (None, "", 0) for v in (stop, target, time_exit))
    items.append(_item("exit", "pass" if has_exit else "fail",
                       f"stop {stop:,.2f}, target {target:,.2f}, exit by {time_exit}" if has_exit else "no complete exit plan"))
    # 3 small risk
    if not affordable or max_loss is None:
        items.append(_item("risk", "fail", "one unit already risks more than your limit" if budget else "position not sized"))
    else:
        ok = max_loss <= (budget or 0) + 1e-6
        items.append(_item("risk", "pass" if ok else "fail", f"loss at stop ₹{max_loss:,.0f} vs limit ₹{budget:,.0f}"))
    # 4 liquidity
    if spread_pct is None:
        items.append(_item("liquid", "check", "check the bid-ask spread in your broker app (under 2%)"))
    else:
        items.append(_item("liquid", "pass" if spread_pct <= 0.02 else "fail", f"spread {spread_pct:.1%} of the price"))
    # 5 events
    if results_known and results_date and exit_date and results_date <= exit_date:
        items.append(_item("events", "fail", f"results on {results_date:%d %b}, before your exit on {exit_date:%d %b}"))
    else:
        base = (f"next results {results_date:%d %b}, after your exit" if results_known and results_date
                else "no results date found" if results_known else "no company results apply")
        items.append(_item("events", "check", f"{base}; confirm no {event_note} before your exit"))
    # 6 timing
    if not intraday:
        items.append(_item("timing", "na", "held for days, not an intraday trade"))
    else:
        now = now or datetime.now(IST)
        opn, avoid, out = SESSIONS[session]
        t = now.timetz().replace(tzinfo=None)
        if now.weekday() >= 5 or t < opn or t >= out:
            items.append(_item("timing", "fail", f"outside trading hours (enter after {avoid:%H:%M}, out by {out:%H:%M})"))
        elif t < avoid:
            items.append(_item("timing", "fail", f"first 15 minutes: wait until {avoid:%H:%M}"))
        else:
            left = datetime.combine(now.date(), out) - datetime.combine(now.date(), t)
            items.append(_item("timing", "pass" if left >= timedelta(minutes=30) else "fail",
                               f"{int(left.total_seconds() // 60)} minutes until the {out:%H:%M} exit"))
    passed = sum(i["status"] == "pass" for i in items)
    failed = [i for i in items if i["status"] == "fail"]
    to_check = [i for i in items if i["status"] == "check"]
    applicable = sum(i["status"] != "na" for i in items)
    if failed:
        verdict = "Do not trade: " + ", ".join(i["title"].lower() for i in failed) + (" failed" if len(failed) > 1 else " failed")
    elif to_check:
        verdict = "Only if you can tick: " + ", ".join(i["title"].lower() for i in to_check)
    else:
        verdict = "All checks pass"
    return {"items": items, "passed": passed, "applicable": applicable, "failed": len(failed),
            "to_check": len(to_check), "ok": not failed and not to_check, "verdict": verdict}


def short_line(cl: dict) -> str:
    """One line for the daily message: ✓ pass, ✕ fail, ? check yourself, – not applicable."""
    mark = {"pass": "✓", "fail": "✕", "check": "?", "na": "–"}
    return "CHECKLIST " + " ".join(f"{mark[i['status']]}{i['title'].split()[-1].lower()}" for i in cl["items"]) + f" · {cl['verdict']}"
