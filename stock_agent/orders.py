"""Order record: every order with the price you planned next to the price you got.

Real and practice broker orders come from My trades (each keeps the live quote and limit price at the moment you
pressed Place, the fill, and every exit with the stop or target it was aiming for). Paper trades keep the quoted
price, the simulated fill and, for stop and target exits, the planned level.

Slippage is counted against you: paying more than planned on a buy, or getting less on a sell, is positive.
A stop that fills below its level because the price gapped through it shows up here as slippage.
"""
from __future__ import annotations

import statistics


def _row(when, source, practice, symbol, action, qty, planned, filled, planned_what) -> dict | None:
    if not filled or not planned or not qty:
        return None
    buy = action.startswith("Buy")
    per_unit = (filled - planned) if buy else (planned - filled)
    return {"time": when, "source": source, "practice": practice, "symbol": symbol, "action": action, "qty": qty,
            "planned": float(planned), "planned_what": planned_what, "filled": float(filled),
            "slip": float(per_unit), "slip_pct": float(per_unit / planned), "slip_rupees": float(per_unit * qty)}


def _broker_rows(e: dict) -> list[dict]:
    rec = e.get("kite") or {}
    if not rec:
        return []
    from .brokers import LABELS

    src = LABELS.get(rec.get("broker") or "kite", "Kite")
    sym = rec.get("tradingsymbol") or e.get("contract")
    plan = rec.get("planned") or {}
    out = []
    planned = plan.get("quote") or plan.get("limit") or rec.get("buy_price")
    what = "live ask when you pressed Place" if plan.get("quote") else "your limit price"
    out.append(_row(plan.get("at") or e.get("opened"), src, bool(rec.get("practice")), sym, "Buy",
                    rec.get("filled_qty"), planned, rec.get("avg_price"), what))
    legs = rec.get("gtt_legs") or rec.get("exit_legs") or []
    for x in rec.get("exits") or []:
        price = x.get("price")
        if not price:
            continue
        # which level was it aiming for: the nearer of the legs' stop and target
        levels = [(abs(price - lv), lv, k) for leg in legs for k, lv in (("stop", leg.get("stop")), ("target", leg.get("target"))) if lv]
        if x.get("via") == "Exit now" or not levels:
            planned_x, what_x, action = rec.get("avg_price"), "your buy price (exit now)", "Sell (exit now)"
        else:
            _, planned_x, kind = min(levels)
            what_x, action = f"{kind} level", f"Sell at {kind}"
        out.append(_row(e.get("closed") or plan.get("at"), src, bool(rec.get("practice")), sym, action, x.get("qty"),
                        planned_x, price, what_x))
    return [r for r in out if r]


def _paper_rows(p: dict, closed: bool) -> list[dict]:
    sym = p.get("contract") or (p.get("symbol") or "").replace(".NS", "")
    if p.get("instrument") == "option":
        sym = f"{(p.get('symbol') or '').replace('.NS', '')} {p.get('strike', ''):g} {p.get('option_type', '')}".strip()
    short = p.get("side") == "short"
    out = [_row(p.get("entry_time"), "Paper", True, sym, "Sell" if short else "Buy", p.get("qty"),
                p.get("quote"), p.get("entry_price"), "quoted price")]
    if closed:
        planned = p.get("exit_planned") or p.get("exit_quote")
        what = f"{p.get('exit_reason', 'exit').lower()} level" if p.get("exit_planned") else "quoted price"
        out.append(_row(p.get("exit_time"), "Paper", True, sym, ("Buy back" if short else "Sell") + f" ({p.get('exit_reason', 'exit')})",
                        p.get("qty"), planned, p.get("exit_price"), what))
    return [r for r in out if r]


def build(journal: list[dict], paper: dict | None) -> dict:
    rows = [r for e in journal for r in _broker_rows(e)]
    if paper:
        rows += [r for p in paper.get("positions", []) for r in _paper_rows(p, False)]
        rows += [r for p in paper.get("closed", []) for r in _paper_rows(p, True)]
    rows.sort(key=lambda r: str(r["time"] or ""), reverse=True)
    summary = {}
    for src in sorted({r["source"] + (" practice" if r["practice"] and r["source"] != "Paper" else "") for r in rows}):
        rs = [r for r in rows if r["source"] + (" practice" if r["practice"] and r["source"] != "Paper" else "") == src]
        pcts = [r["slip_pct"] for r in rs]
        summary[src] = {"orders": len(rs), "avg_slip_pct": statistics.fmean(pcts), "median_slip_pct": statistics.median(pcts),
                        "total_rupees": sum(r["slip_rupees"] for r in rs), "worst": max(rs, key=lambda r: r["slip_pct"])}
    return {"rows": rows, "summary": summary}
