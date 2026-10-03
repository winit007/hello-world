"""Capital-gains tax on listed Indian shares and equity funds, and what to sell before 31 March.

Rules (Finance Act 2024, sales from 23 July 2024):
  short-term   held 12 months or less: 20%
  long-term    held more than 12 months: 12.5% on the year's net long-term gains above ₹1.25 lakh
  set-off      short-term losses reduce short-term gains first, then long-term gains; long-term losses reduce
               only long-term gains; what is left can be carried forward 8 years (if your return is filed on time)
  F&O          futures and options profits are business income at your slab rate, not capital gains, and the
               two cannot be offset against each other here
Cess (4%) and surcharge are left out. This is a planning aid, not tax advice.

Harvesting ideas, each with the tax it saves and what the sale would cost:
  loss harvesting   sell a holding that is below its buy price to book the loss against this year's taxable gains;
                    buy it back the next day if you still want it (India has no wash-sale rule, but the buy date
                    and price reset)
  gain harvesting   book long-term gains up to the unused ₹1.25 lakh exemption and buy back: no tax now, and a
                    higher buy price means less tax later
  wait              a profitable holding a few weeks short of 12 months: selling after that date is taxed at 12.5%
                    instead of 20%
"""
from __future__ import annotations

from datetime import date, timedelta

STCG, LTCG, EXEMPT = 0.20, 0.125, 125_000.0


def fy_bounds(d: date) -> tuple[date, date]:
    start = date(d.year if d.month >= 4 else d.year - 1, 4, 1)
    return start, date(start.year + 1, 3, 31)


def long_term(buy: date, sell: date) -> bool:
    """Held for more than 12 months."""
    try:
        anniversary = buy.replace(year=buy.year + 1)
    except ValueError:                       # 29 February
        anniversary = buy + timedelta(days=365)
    return sell > anniversary


def lt_date(buy: date) -> date:
    try:
        return buy.replace(year=buy.year + 1) + timedelta(days=1)
    except ValueError:
        return buy + timedelta(days=366)


def tax(st: float, lt: float, cf_st: float = 0.0, cf_lt: float = 0.0) -> dict:
    """Tax on a year's net short-term (st) and long-term (lt) results; losses negative. cf_* = losses brought
    forward from earlier years (positive numbers)."""
    st_g, st_l = max(st, 0.0), max(-st, 0.0)
    lt_g, lt_l = max(lt, 0.0), max(-lt, 0.0)
    # this year's losses: long-term losses only against long-term gains; short-term losses against either
    use = min(lt_l, lt_g)
    lt_g, lt_l = lt_g - use, lt_l - use
    use = min(st_l, st_g)
    st_g, st_l = st_g - use, st_l - use
    use = min(st_l, lt_g)
    lt_g, st_l = lt_g - use, st_l - use
    # brought-forward losses, same rules
    use = min(cf_lt, lt_g)
    lt_g, cf_lt = lt_g - use, cf_lt - use
    use = min(cf_st, st_g)
    st_g, cf_st = st_g - use, cf_st - use
    use = min(cf_st, lt_g)
    lt_g, cf_st = lt_g - use, cf_st - use
    exempt_used = min(lt_g, EXEMPT)
    t = st_g * STCG + (lt_g - exempt_used) * LTCG
    return {"taxable_st": st_g, "taxable_lt": lt_g, "exempt_used": exempt_used, "exempt_left": EXEMPT - exempt_used,
            "tax": t, "carry_st": st_l + cf_st, "carry_lt": lt_l + cf_lt}


def realised(sales: list[dict], fy_start: date, fy_end: date) -> dict:
    st = lt = 0.0
    rows = []
    for s in sales:
        d = date.fromisoformat(s["date"])
        if not fy_start <= d <= fy_end:
            continue
        gain = (float(s["price"]) - float(s["buy_price"])) * float(s["qty"])
        is_lt = long_term(date.fromisoformat(s["buy_date"]), d)
        lt, st = (lt + gain, st) if is_lt else (lt, st + gain)
        rows.append({**s, "gain": gain, "long_term": is_lt})
    return {"st": st, "lt": lt, "rows": rows}


def plan(holdings: list[dict], prices: dict[str, float], sales: list[dict], extra_st: float = 0.0, extra_lt: float = 0.0,
         cf_st: float = 0.0, cf_lt: float = 0.0, fno: float = 0.0, today: date | None = None,
         sell_cost=lambda value: value * 0.0012) -> dict:
    today = today or date.today()
    fy0, fy1 = fy_bounds(today)
    done = realised(sales, fy0, fy1)
    st0, lt0 = done["st"] + extra_st, done["lt"] + extra_lt
    base = tax(st0, lt0, cf_st, cf_lt)
    rows = []
    for h in holdings:
        px = prices.get(h["symbol"])
        if px is None:
            rows.append({**h, "price": None})
            continue
        bd = date.fromisoformat(h["buy_date"])
        gain = (px - float(h["buy_price"])) * float(h["qty"])
        is_lt = long_term(bd, today)
        rows.append({**h, "price": px, "value": px * float(h["qty"]), "gain": gain, "long_term": is_lt,
                     "long_term_from": lt_date(bd).isoformat(), "days_to_lt": max((lt_date(bd) - today).days, 0)})
    ideas = []
    # 1. loss harvesting: each losing holding on its own, ranked by the tax it saves this year
    for r in rows:
        if r.get("price") is None or r["gain"] >= 0:
            continue
        st, lt = (st0, lt0 + r["gain"]) if r["long_term"] else (st0 + r["gain"], lt0)
        after = tax(st, lt, cf_st, cf_lt)
        saved = base["tax"] - after["tax"]
        cost = 2 * sell_cost(r["value"])            # sell and buy back
        carry = (after["carry_st"] + after["carry_lt"]) - (base["carry_st"] + base["carry_lt"])
        # only when it cuts this year's tax: a carried-forward loss must later be set off against gains that might
        # have been tax-free under the exemption anyway
        if saved > cost:
            ideas.append({"kind": "loss", "id": r["id"], "symbol": r["symbol"], "qty": r["qty"], "gain": r["gain"],
                          "tax_saved": saved, "cost": cost, "carry_forward": carry,
                          "text": (f"Sell and buy back {r['symbol'].replace('.NS', '')}: books a {'long' if r['long_term'] else 'short'}-term "
                                   f"loss of ₹{-r['gain']:,.0f}" + (f", saving ₹{saved:,.0f} of tax this year" if saved > 0 else "")
                                   + (f" and carrying ₹{carry:,.0f} of loss to later years" if carry > 0 else "") + ".")})
    # 2. gain harvesting: long-term gains up to the unused exemption, tax-free
    room = base["exempt_left"] if base["taxable_lt"] < EXEMPT else 0.0
    for r in sorted((r for r in rows if r.get("price") is not None and r["long_term"] and r["gain"] > 0), key=lambda r: -r["gain"]):
        if room <= 1000:
            break
        per_share = r["price"] - float(r["buy_price"])
        qty = min(float(r["qty"]), room // per_share) if per_share > 0 else 0
        if r["symbol"].upper().endswith((".NS", ".BO")):
            qty = float(int(qty))
        if qty <= 0:
            continue
        g = qty * per_share
        room -= g
        ideas.append({"kind": "gain", "id": r["id"], "symbol": r["symbol"], "qty": qty, "gain": g, "tax_saved": g * LTCG,
                      "cost": 2 * sell_cost(qty * r["price"]),
                      "text": (f"Sell and buy back {qty:g} of {r['symbol'].replace('.NS', '')}: books ₹{g:,.0f} of long-term gain "
                               f"inside the ₹1.25 lakh exemption (no tax), and raises your buy price so about ₹{g * LTCG:,.0f} less "
                               f"tax is due when you finally sell.")})
    # 3. wait: profitable short-term holdings close to turning long-term
    for r in rows:
        if r.get("price") is not None and not r["long_term"] and r["gain"] > 0 and r["days_to_lt"] <= 90:
            ideas.append({"kind": "wait", "id": r["id"], "symbol": r["symbol"], "qty": r["qty"], "gain": r["gain"],
                          "tax_saved": r["gain"] * (STCG - LTCG), "cost": 0.0,
                          "text": (f"Hold {r['symbol'].replace('.NS', '')} until {date.fromisoformat(r['long_term_from']):%d %b %Y} "
                                   f"({r['days_to_lt']} days) if you plan to sell: its ₹{r['gain']:,.0f} gain is then taxed at 12.5% "
                                   f"instead of 20%.")})
    ideas.sort(key=lambda i: -(i["tax_saved"] - i["cost"]))
    unreal_st = sum(r["gain"] for r in rows if r.get("price") is not None and not r["long_term"])
    unreal_lt = sum(r["gain"] for r in rows if r.get("price") is not None and r["long_term"])
    return {"fy": f"{fy0.year}-{str(fy1.year)[2:]}", "fy_end": fy1.isoformat(), "days_left": (fy1 - today).days,
            "realised": done, "realised_st": st0, "realised_lt": lt0, "tax_now": base,
            "if_sold_all": tax(st0 + unreal_st, lt0 + unreal_lt, cf_st, cf_lt), "unrealised_st": unreal_st,
            "unrealised_lt": unreal_lt, "holdings": rows, "ideas": ideas, "fno": fno,
            "rates": {"stcg": STCG, "ltcg": LTCG, "exempt": EXEMPT}}
