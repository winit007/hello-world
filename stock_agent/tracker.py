"""Track record: what each day's picks would have made if you had taken them.

Every scan's picks are saved in a ledger. For each pick we then replay the following days with the
real daily candles and the plan's own rules:

  * Entry: buy at the NEXT trading day's open (the picks arrive after the close).
  * Targets: if the day's high (CALL) or low (PUT) reaches Target 1 / Target 2, that part is sold there.
    With 2+ lots half exits at Target 1 and the rest at Target 2; a single lot exits fully at Target 1
    (the same split the Kite GTTs use).
  * Stop: as soon as the stock TRADES through the stop (day's low for a CALL, high for a PUT), everything
    left is sold at the stop level, or at the open if the stock gapped through it. When the stop and a
    target are both touched on the same day the stop is assumed to have come first (conservative).
  * Time exit: whatever is left is sold at the close of the plan's exit date.

There is no free history of NSE option prices, so option values are estimated with Black-Scholes from
the stock price, using the volatility implied by the pick's own premium estimate. Real fills, spreads
and charges will differ; treat the rupee figures as estimates and the win/loss pattern as the signal.

Picks you can afford are counted with their real lot count ("your account"); picks that do not fit
your risk limit are counted as one lot and reported separately.
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pandas as pd

from .options import bs_price, implied_vol
from .sizing import _round_tick, money

INDIA_RATE, US_RATE = 0.065, 0.04


# --------------------------------------------------------------------------- ledger
def load_ledger(path: Path) -> list[dict]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []


def save_ledger(path: Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r["as_of"], r.get("rank", 0)))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def pick_record(p, as_of: str, rank: int, replayed: bool = False) -> dict | None:
    plan = p.plan
    if plan is None or plan.lot_size is None:
        return None
    return {
        "as_of": as_of, "rank": rank, "ticker": p.ticker, "side": p.side, "pattern": p.pattern,
        "success": round(p.success, 4), "baseline": round(p.baseline, 4), "close": round(p.last_close, 2),
        "strike": plan.strike, "expiry": plan.expiry.isoformat(), "premium": plan.premium,
        "lot_size": plan.lot_size, "lots": plan.lots, "stop": plan.stop_underlying,
        "target1": plan.target1_underlying, "target2": plan.target2_underlying,
        "time_exit": plan.time_stop.isoformat(), "replayed": replayed, "instrument": plan.instrument,
    }


def record(path: Path, picks, stats: dict, replayed: bool = False) -> list[dict]:
    """Store one scan's picks, replacing any earlier record of the same market day."""
    as_of = stats.get("as_of")
    if not as_of:
        return load_ledger(path)
    rows = [r for r in load_ledger(path) if r["as_of"] != as_of]
    rows += [r for r in (pick_record(p, as_of, i + 1, replayed) for i, p in enumerate(picks)) if r]
    save_ledger(path, rows)
    return rows


# --------------------------------------------------------------------------- simulation
def _t(expiry: date, d: date) -> float:
    return max((expiry - d).days, 0.5) / 365


def simulate(rec: dict, df: pd.DataFrame) -> dict:
    """Day-by-day outcome of one pick. Rupee values are for the whole position."""
    india = rec["ticker"].upper().endswith((".NS", ".BO"))
    r = INDIA_RATE if india else US_RATE
    kind = "call" if rec["side"] == "CALL" else "put"
    call = kind == "call"
    expiry = date.fromisoformat(rec["expiry"])
    as_of = date.fromisoformat(rec["as_of"])
    lots = int(rec["lots"]) if int(rec["lots"]) > 0 else 1
    lot = int(rec["lot_size"])
    out = {**rec, "affordable": int(rec["lots"]) > 0, "sim_lots": lots, "qty": lots * lot, "status": "waiting",
           "entry_date": None, "entry_premium": None, "exits": [], "daily": {}, "pnl": 0.0, "value": None}
    if rec.get("instrument") == "stock":
        price = lambda s, d: round(float(s), 2)   # shares: the position is worth the share price
    else:
        iv = implied_vol(rec["premium"], rec["close"], rec["strike"], _t(expiry, as_of), kind) or 0.3
        price = lambda s, d: max(_round_tick(bs_price(s, rec["strike"], _t(expiry, d), iv, kind, r)), 0.05)

    bars = df[df.index > pd.Timestamp(as_of)]
    if bars.empty:
        return out
    if lots >= 2:
        first = math.ceil(lots / 2)
        legs = [{"qty": first * lot, "target": rec["target1"], "label": "Target 1"},
                {"qty": (lots - first) * lot, "target": rec["target2"], "label": "Target 2"}]
    else:
        legs = [{"qty": lot, "target": rec["target1"], "label": "Target 1"}]
    for leg in legs:
        leg["open"] = True

    entry_day = bars.index[0].date()
    first_open = float(bars["Open"].iloc[0])
    if (first_open <= rec["stop"]) if call else (first_open >= rec["stop"]):
        # the stock opened beyond the stop on the morning you would buy: the setup is broken, skip it
        out.update(entry_date=entry_day.isoformat(), status="Skipped", last_date=entry_day.isoformat())
        return out
    entry = price(first_open, entry_day)
    out.update(entry_date=entry_day.isoformat(), entry_premium=entry, status="open")
    cost = entry * lots * lot
    prev_value = cost
    realized = 0.0
    time_exit = date.fromisoformat(rec["time_exit"])
    for ts, bar in bars.iterrows():
        d = ts.date()
        o, h, l, c = (float(bar[k]) for k in ("Open", "High", "Low", "Close"))
        day_real = 0.0
        stop_hit = l <= rec["stop"] if call else h >= rec["stop"]
        if stop_hit:  # intraday stop, checked first; a gap through it fills at the open
            fill_at = min(o, rec["stop"]) if call else max(o, rec["stop"])
            px = price(fill_at, d)
            for leg in legs:
                if leg["open"]:
                    leg["open"] = False
                    day_real += px * leg["qty"]
                    out["exits"].append({"date": d.isoformat(), "qty": leg["qty"], "price": px, "reason": "Stop"})
        for leg in legs:
            if not leg["open"]:
                continue
            hit = h >= leg["target"] if call else l <= leg["target"]
            if hit:
                fill_at = max(o, leg["target"]) if call else min(o, leg["target"])  # gap through = better fill
                px = price(fill_at, d)
                leg["open"] = False
                day_real += px * leg["qty"]
                out["exits"].append({"date": d.isoformat(), "qty": leg["qty"], "price": px, "reason": leg["label"]})
        last_day = d >= time_exit or d >= expiry
        for leg in legs:
            if leg["open"] and last_day:
                px = price(c, d)
                leg["open"] = False
                day_real += px * leg["qty"]
                out["exits"].append({"date": d.isoformat(), "qty": leg["qty"], "price": px, "reason": "Time exit"})
        realized += day_real
        value = sum(price(c, d) * leg["qty"] for leg in legs if leg["open"])
        out["daily"][d.isoformat()] = round(day_real + value - prev_value, 2)
        prev_value = value
        out["value"] = round(value, 2)
        if not any(leg["open"] for leg in legs):
            break
    out["pnl"] = round(realized + (out["value"] or 0) - cost, 2)
    if not any(leg["open"] for leg in legs):
        reasons = {e["reason"] for e in out["exits"]}
        out["status"] = ("Stop" if "Stop" in reasons and len(reasons) == 1 else
                         "Target 2" if "Target 2" in reasons else
                         "Target 1" if "Target 1" in reasons and "Stop" not in reasons else
                         "Target 1 + Stop" if "Target 1" in reasons else "Time exit")
    out["last_date"] = max(out["daily"]) if out["daily"] else None
    return out


def evaluate(rows: list[dict], prices: dict[str, pd.DataFrame]) -> dict:
    results = [simulate(r, prices[r["ticker"]]) for r in rows if r["ticker"] in prices]
    days = sorted({d for x in results for d in x["daily"]})

    def agg(sel):
        by_day = {d: round(sum(x["daily"].get(d, 0.0) for x in sel), 2) for d in days}
        closed = [x for x in sel if x["status"] not in ("open", "waiting", "Skipped")]
        wins = [x for x in closed if x["pnl"] > 0]
        return {"by_day": by_day, "total": round(sum(x["pnl"] for x in sel), 2), "trades": len(sel),
                "closed": len(closed), "wins": len(wins), "win_rate": (len(wins) / len(closed)) if closed else None,
                "open": sum(1 for x in sel if x["status"] == "open")}

    entered = [x for x in results if x["status"] not in ("waiting", "Skipped")]
    return {"results": results, "days": days, "latest_day": days[-1] if days else None,
            "account": agg([x for x in entered if x["affordable"]]), "all": agg(entered)}


# --------------------------------------------------------------------------- replay
def replay(tickers: list[str], days: int, prices: dict[str, pd.DataFrame], ledger_path: Path, horizon: int = 5,
           top: int = 5, capital: float | None = None, risk_pct: float = 0.02, cache_dir=None, progress=None,
           skip_existing: bool = True) -> int:
    """Rebuild the picks of the last `days` market days from prices alone (no news) and store them."""
    from . import screener
    from .tradetest import trade_outcomes

    have = {r["as_of"] for r in load_ledger(ledger_path)}
    # trade outcomes never look ahead (each counts only once its exit date has passed), so compute them
    # once on the full history and let every replayed day filter them by date
    outcomes = {t: trade_outcomes(df, t, horizon) for t, df in prices.items()}
    calendar = sorted({ts for df in prices.values() for ts in df.index[-(days + 1):]})[-(days + 1):-1]
    done = 0
    for i, day in enumerate(calendar):
        key = day.date().isoformat()
        if skip_existing and key in have:
            continue
        if progress:
            progress(f"Replaying picks for {key}", i / max(len(calendar), 1))
        cut = {t: df[df.index <= day] for t, df in prices.items() if (df.index <= day).sum() >= 250}
        picks, _, stats = screener.screen(list(cut), horizon=horizon, top=top, news_weight=0.0, capital=capital,
                                          risk_pct=risk_pct, prices=cut, use_chain=False, cache_dir=cache_dir,
                                          log=lambda *a: None, outcomes={t: outcomes[t] for t in cut})
        record(ledger_path, picks, {**stats, "as_of": key}, replayed=True)
        done += 1
    return done


# --------------------------------------------------------------------------- reports
def _fmt_day(d: str) -> str:
    return pd.Timestamp(d).strftime("%d %b")


def render_brief(ev: dict, recent_days: int = 5) -> str:
    """Notification-sized summary: the latest day's P&L, running totals, each recent pick's outcome."""
    if not ev["days"]:
        return "What-if P&L: no picks have reached their entry day yet."
    last = ev["latest_day"]
    acc, al = ev["account"], ev["all"]
    c = "₹" if any(x["ticker"].upper().endswith((".NS", ".BO")) for x in ev["results"]) else "$"
    lines = [f"What-if P&L if you had taken the picks (model estimate, before charges)",
             f"{_fmt_day(last)}: your account {money(acc['by_day'].get(last, 0), c)} · all picks {money(al['by_day'].get(last, 0), c)}",
             f"Since {_fmt_day(ev['days'][0])}: your account {money(acc['total'], c)} ({acc['wins']}/{acc['closed']} closed won) · "
             f"all picks at 1 lot {money(al['total'], c)} ({al['wins']}/{al['closed']} won)"]
    pick_days = sorted({x["as_of"] for x in ev["results"]})[-recent_days:]
    for x in sorted((x for x in ev["results"] if x["as_of"] in pick_days), key=lambda x: (x["as_of"], x["rank"])):
        if x["status"] in ("waiting", "Skipped"):
            continue
        tag = "" if x["affordable"] else " (1 lot, not affordable)"
        today = x["daily"].get(last)
        today_txt = f", {_fmt_day(last)} {money(today, c)}" if today is not None and x["status"] == "open" else ""
        what = "shares" if x.get("instrument") == "stock" else f"{x['strike']:g}{'CE' if x['side'] == 'CALL' else 'PE'}"
        lines.append(f"• {x['ticker'].split('.')[0]} {what} picked "
                     f"{_fmt_day(x['as_of'])}: {x['status']}{today_txt}, total {money(x['pnl'], c)}{tag}")
    return "\n".join(lines)


def render_markdown(ev: dict) -> str:
    out = ["# Track record", "", "_If you had taken every pick. Option prices are model estimates; real fills, spreads "
           "and charges differ._", ""]
    if not ev["days"]:
        return "\n".join(out + ["No picks have reached their entry day yet."])
    c = "₹"
    acc, al = ev["account"], ev["all"]
    out += [f"| | Your account | All picks (1 lot) |", "|---|---|---|",
            f"| Total P&L | {money(acc['total'], c)} | {money(al['total'], c)} |",
            f"| Closed / won | {acc['closed']} / {acc['wins']} | {al['closed']} / {al['wins']} |",
            f"| Open | {acc['open']} | {al['open']} |", "",
            "| Day | Your account | All picks |", "|---|---|---|"]
    out += [f"| {_fmt_day(d)} | {money(acc['by_day'][d], c)} | {money(al['by_day'][d], c)} |" for d in ev["days"]]
    out += ["", "| Picked | Stock | Contract | Lots | Bought | Outcome | P&L |", "|---|---|---|---|---|---|---|"]
    for x in sorted(ev["results"], key=lambda x: (x["as_of"], x["rank"]), reverse=True):
        bought = (f"{x['entry_premium']:.2f} on {_fmt_day(x['entry_date'])}" if x["entry_premium"] is not None
                  else "not bought" if x["status"] == "Skipped" else "tomorrow")
        out.append(f"| {_fmt_day(x['as_of'])}{' (replay)' if x.get('replayed') else ''} | {x['ticker'].split('.')[0]} | "
                   f"{'shares' if x.get('instrument') == 'stock' else str(x['strike']) + (' CE' if x['side'] == 'CALL' else ' PE')} | {x['sim_lots']}{'' if x['affordable'] else '*'} | "
                   f"{bought} | {x['status']} | {'–' if x['status'] in ('waiting', 'Skipped') else money(x['pnl'], c)} |")
    out += ["", "\\* not affordable at your risk limit; counted as 1 lot under All picks only."]
    return "\n".join(out)
