"""Portfolio rebalancing: compare what you hold with your target mix and say what to buy or sell.

Holdings are rows of (name, asset class, and either a value in rupees or a Yahoo symbol plus quantity,
in which case today's price is fetched; US-dollar symbols such as BTC-USD are converted to rupees).
Asset classes: equity, debt, gold, crypto, cash, other.

Two ways back to target:
  * full rebalance: sell the overweight classes and buy the underweight ones, but only when some class
    has drifted more than the band (default 5 percentage points); small drift is left alone because
    every sale costs charges and possibly capital-gains tax
  * new money only: put the next investment into the underweight classes first, selling nothing
Within an overweight class the sale is spread across its holdings in proportion to their size.
"""
from __future__ import annotations

from dataclasses import dataclass

from .data import DEFAULT_CACHE, _cache_path, read_cached, refresh_many

CLASSES = ["equity", "debt", "gold", "crypto", "cash", "other"]


@dataclass
class Holding:
    name: str
    asset_class: str
    value: float | None = None
    symbol: str | None = None
    quantity: float | None = None


def price_holdings(holdings: list[Holding], offline: bool = False, cache_dir=DEFAULT_CACHE) -> list[dict]:
    syms = sorted({h.symbol.strip().upper() for h in holdings if h.symbol and h.quantity})
    need_fx = any(not s.endswith((".NS", ".BO")) for s in syms)
    if syms and not offline:
        refresh_many(syms + (["USDINR=X"] if need_fx else []), "1mo", "1d", cache_dir)
    fx = 1.0
    if need_fx:
        try:
            fx = float(read_cached(_cache_path(cache_dir, "USDINR=X", "1mo", "1d"))["Close"].iloc[-1])
        except Exception:
            fx = 95.0
    out = []
    for h in holdings:
        row = {"name": h.name or h.symbol or "holding", "asset_class": (h.asset_class or "other").lower(),
               "symbol": (h.symbol or "").upper() or None, "quantity": h.quantity, "price": None, "value": h.value,
               "note": ""}
        if row["asset_class"] not in CLASSES:
            row["asset_class"] = "other"
        if row["symbol"] and h.quantity:
            try:
                px = float(read_cached(_cache_path(cache_dir, row["symbol"], "1mo", "1d"))["Close"].iloc[-1])
                inr = px if row["symbol"].endswith((".NS", ".BO")) else px * fx
                row.update(price=round(inr, 2), value=round(inr * float(h.quantity), 2))
            except Exception:
                row["note"] = "price not found; enter its value instead"
        row["value"] = float(row["value"] or 0)
        out.append(row)
    return out


def recommend(holdings: list[dict], target: dict[str, float], band: float = 0.05, new_money: float = 0.0) -> dict:
    """Current vs target weights, and the trades for a full rebalance or for new money only."""
    total = sum(h["value"] for h in holdings)
    tsum = sum(target.values()) or 1
    target = {k: v / tsum for k, v in target.items() if v > 0}
    classes = sorted(set(target) | {h["asset_class"] for h in holdings}, key=lambda c: CLASSES.index(c) if c in CLASSES else 99)
    cur = {c: sum(h["value"] for h in holdings if h["asset_class"] == c) for c in classes}
    rows = []
    for c in classes:
        w = cur[c] / total if total else 0.0
        t = target.get(c, 0.0)
        rows.append({"asset_class": c, "value": cur[c], "weight": w, "target": t, "drift": w - t,
                     "target_value": t * total, "trade": t * total - cur[c]})
    max_drift = max((abs(r["drift"]) for r in rows), default=0.0)
    needed = max_drift > band

    # proportional sells inside overweight classes
    sells = []
    for r in rows:
        if needed and r["trade"] < 0 and r["value"] > 0:
            for h in holdings:
                if h["asset_class"] == r["asset_class"] and h["value"] > 0:
                    amt = -r["trade"] * h["value"] / r["value"]
                    qty = None
                    if h.get("price"):
                        qty = amt / h["price"]
                        # NSE shares and ETFs trade in whole units; coins can be fractional
                        qty = round(qty) if (h.get("symbol") or "").endswith((".NS", ".BO")) else round(qty, 6)
                    sells.append({"name": h["name"], "asset_class": h["asset_class"], "amount": round(amt, 2),
                                  "quantity": qty})
    buys = [{"asset_class": r["asset_class"], "amount": round(r["trade"], 2)} for r in rows if needed and r["trade"] > 0]

    # new money: fill the biggest gaps (measured after adding the money) first
    new_split = {}
    if new_money > 0:
        after = total + new_money
        gaps = {r["asset_class"]: max(target.get(r["asset_class"], 0) * after - r["value"], 0) for r in rows}
        gap_sum = sum(gaps.values())
        if gap_sum <= new_money:
            new_split = {c: g for c, g in gaps.items() if g > 0}
            rest = new_money - gap_sum
            for c, t in target.items():
                new_split[c] = new_split.get(c, 0) + rest * t
        else:
            new_split = {c: new_money * g / gap_sum for c, g in gaps.items() if g > 0}
        new_split = {c: round(v, 2) for c, v in new_split.items() if v > 0.5}

    if not total:
        verdict = "Add your holdings to see a recommendation."
    elif needed:
        worst = max(rows, key=lambda r: abs(r["drift"]))
        verdict = (f"Rebalance: {worst['asset_class']} is {worst['drift']:+.0%} off target (more than the "
                   f"{band:.0%} band). Selling may attract capital-gains tax and charges; if new money is coming, "
                   f"the 'new money only' split fixes the drift without selling.")
    else:
        verdict = f"No rebalance needed: every class is within {band:.0%} of target."
    return {"total": total, "rows": rows, "max_drift": max_drift, "needed": needed, "band": band,
            "sells": sells, "buys": buys, "new_money": new_money, "new_split": new_split, "verdict": verdict,
            "target": target}
