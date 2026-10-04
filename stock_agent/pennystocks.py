"""Penny stocks for the long run: an automatic screen for low-priced NSE shares that are more than a lottery ticket.

Most low-priced shares are cheap for a reason. This screen does not look for the next 10x. It removes the
traps (shells nobody can sell, pump-and-dump runs, loss makers, over-borrowed and pledged companies) and ranks
what is left on quality, growth and steadiness, so a small long-term bucket is spread over the better ones.

Every weekday it reads NSE's daily file of all ~2,600 shares (one download), keeps the cheap ones that trade
enough to be sold, loads their ten-year prices and company results, and runs three groups of rules:

  1. Price and liquidity   a price between the floor and the cap, and median traded value of at least the minimum
  2. Safety                three years of history, in an uptrend, not up 100% in 3 months or 300% in a year, a worst
                           fall in the last year better than -65%, not hitting a circuit limit every few days,
                           and a market value of at least ₹150 crore
  3. Quality               profitable last year, ROE of at least 8%, debt below equity, sales not shrinking, and
                           promoters holding at least 20% with no more than half of it pledged

Survivors are ranked by a long-run score (return on equity and capital, growth, low debt, promoter holding and
buying, momentum, calm price, valuation) and the top few get a small equal slice of a bucket you size
(10% of your capital by default). Stocks that fail exactly one rule are listed as near misses.

The history test (`backtest`) can only check the price and liquidity gates, because free company results cover
four years, and it cannot see companies that were delisted, so it flatters penny stocks. The page says so.
"""
from __future__ import annotations

import csv
import io
import math
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import re

import numpy as np
import pandas as pd
import requests

from . import attribution as A
from . import fundamentals as F
from . import myscreen as M
from .data import _cache_path, read_cached, refresh_many
from .screener import load_universe
from .universes import get_universe, industries, nse_equity

BHAV = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
NOT_A_COMPANY = re.compile(r"\b(etf|bees|index fund|liquid fund|gilt|bond)\b", re.I)
BENCH = "NIFTYBEES.NS"
SMALLCAP = "NIFTYSMLCAP250.NS"


@dataclass
class PennyRules:
    price_cap: float = 50.0
    price_floor: float = 2.0
    min_lakh: float = 25.0              # median traded value a day, ₹ lakh
    min_years: float = 3.0
    min_mcap_cr: float = 150.0
    uptrend: bool = True
    max_ret_3m: float = 1.0             # up more than 100% in 3 months: probably being pumped
    max_ret_12m: float = 3.0
    max_drawdown: float = -0.65         # worst fall of the last year must be better than this
    max_circuit_days: int = 4           # days in the last 60 that ended at a 5/10/20% price limit
    min_roe: float = 0.08
    max_de: float = 1.0
    min_sales_growth: float = 0.0
    min_promoter: float = 0.20
    max_pledge: float = 0.50            # hard limit; above 10% is flagged
    allow_no_results: bool = False      # let in stocks Yahoo has no company results for
    top: int = 10
    bucket_pct: float = 10.0            # share of your capital to put in this bucket


def is_company(ticker: str, names: dict) -> bool:
    """Index-fund ETFs sit in NSE's equity series too; they have no company results and are not penny stocks."""
    from .risk import is_etf

    return not is_etf(ticker) and not NOT_A_COMPANY.search(names.get(ticker, ""))


def rules_from(d: dict | None) -> PennyRules:
    base = PennyRules()
    for k, v in (d or {}).items():
        if hasattr(base, k) and v is not None:
            setattr(base, k, type(getattr(base, k))(v))
    base.top = int(min(max(base.top, 3), 25))
    base.bucket_pct = float(min(max(base.bucket_pct, 1), 30))
    base.price_cap = float(min(max(base.price_cap, 5), 500))
    base.min_lakh = float(min(max(base.min_lakh, 2), 5000))
    return base


# ----------------------------------------------------------------------------- today's NSE file
def parse_bhavcopy(text: str) -> pd.DataFrame:
    rows = [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(io.StringIO(text))]
    df = pd.DataFrame(rows)
    if df.empty or "CLOSE_PRICE" not in df:
        raise ValueError("Unexpected NSE file")
    df = df[df["SERIES"] == "EQ"].copy()
    num = lambda c: pd.to_numeric(df[c], errors="coerce")
    out = pd.DataFrame({"symbol": df["SYMBOL"] + ".NS", "close": num("CLOSE_PRICE"), "turnover_lakh": num("TURNOVER_LACS"),
                        "deliv_per": num("DELIV_PER")}).dropna(subset=["close"])
    out.attrs["date"] = pd.to_datetime(df["DATE1"].iloc[0], format="%d-%b-%Y", errors="coerce").date() if len(df) else None
    return out.set_index("symbol")


def fetch_bhavcopy(cache_dir: Path, today: date | None = None, offline: bool = False) -> pd.DataFrame | None:
    """The latest daily file of all NSE equities (cached for the day); None when NSE cannot be reached."""
    today = today or date.today()
    folder = Path(cache_dir) / "bhavcopy"
    for back in range(0, 8):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        path = folder / f"{d:%d%m%Y}.csv"
        text = path.read_text() if path.exists() else None
        if text is None and not offline:
            try:
                r = requests.get(BHAV.format(d=f"{d:%d%m%Y}"), headers=UA, timeout=30)
                if r.ok and "CLOSE_PRICE" in r.text[:400]:
                    text = r.text
                    folder.mkdir(parents=True, exist_ok=True)
                    path.write_text(text)
                    for old in sorted(folder.glob("*.csv"))[:-6]:      # keep a week
                        old.unlink(missing_ok=True)
            except requests.RequestException:
                return None
        if text:
            try:
                return parse_bhavcopy(text)
            except ValueError:
                continue
    return None


# ----------------------------------------------------------------------------- rules
def circuit_days(closes: pd.DataFrame, days: int = 60) -> pd.Series:
    """Days in the last `days` that ended within 0.3 points of a 5%, 10% or 20% move (the NSE price limits)."""
    r = closes.pct_change().iloc[-days:].abs()
    hit = pd.DataFrame(False, index=r.index, columns=r.columns)
    for band in (0.05, 0.10, 0.20):
        hit |= (r - band).abs() <= 0.003
    return hit.sum()


def aligned(prices: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Closes and traded values on one calendar. Short gaps (a holiday row some files have, a quiet day) are carried
    forward; a share with no price for over a week is halted or delisted and is dropped."""
    closes = pd.DataFrame({t: d["Close"] for t, d in prices.items()}).sort_index()
    values = pd.DataFrame({t: d["Close"] * d["Volume"] for t, d in prices.items()}).reindex(closes.index)
    last = closes.apply(lambda s: s.last_valid_index())
    fresh = last >= closes.index[-1] - pd.Timedelta(days=7)
    closes, values = closes.loc[:, fresh].ffill(limit=5), values.loc[:, fresh].ffill(limit=5)
    return closes, values


def snapshot_today(closes: pd.DataFrame, values: pd.DataFrame, bench: pd.Series, fund_data: dict) -> pd.DataFrame:
    frames = M.metric_frames(closes, values, bench, fund_data)
    snap = pd.DataFrame({k: f.iloc[-1] for k, f in frames.items()})
    snap["circuit_days"] = circuit_days(closes)
    snap["years"] = closes.notna().sum() / 252
    return snap


def rule_table(r: PennyRules) -> list[dict]:
    """Every rule: id, group, plain label, the test on a snapshot, and why it exists."""
    # a share without any company results fails the "results" rule alone; the quality rules wait for results to exist
    none = lambda s: s["roe"].isna() & s["net_margin"].isna()
    skip = lambda s, ok: ok | none(s)
    return [
        {"id": "price", "group": "Price and liquidity", "label": f"Price between ₹{r.price_floor:g} and ₹{r.price_cap:g}",
         "fn": lambda s: (s["price"] >= r.price_floor) & (s["price"] <= r.price_cap), "col": "price", "fmt": "price",
         "why": "The definition of a penny stock for this screen."},
        {"id": "liquid", "group": "Price and liquidity", "label": f"Trades at least ₹{r.min_lakh:g} lakh a day",
         "fn": lambda s: s["value_cr"] * 100 >= r.min_lakh, "col": "value_cr", "fmt": "crore",
         "why": "If you cannot sell, a stock is worth nothing. Thinly traded shares also have wide spreads."},
        {"id": "history", "group": "Safety", "label": f"At least {r.min_years:g} years of trading",
         "fn": lambda s: s["years"] >= r.min_years, "col": "years", "fmt": "years",
         "why": "New listings have no record, and many low-priced IPOs fade."},
        {"id": "trend", "group": "Safety", "label": "Price above its 200-day average" if r.uptrend else "Trend not checked",
         "fn": (lambda s: s["vs_sma200"] > 0) if r.uptrend else (lambda s: s["price"].notna()), "col": "vs_sma200", "fmt": "pct",
         "why": "Buying after the market has started to agree avoids stocks that are still falling."},
        {"id": "pump", "group": "Safety", "label": f"Not up more than {r.max_ret_3m:.0%} in 3 months or {r.max_ret_12m:.0%} in a year",
         "fn": lambda s: (s["ret_3m"] <= r.max_ret_3m) & (s["ret_12m"] <= r.max_ret_12m), "col": "ret_3m", "fmt": "pct",
         "why": "Parabolic runs in cheap shares are usually followed by crashes."},
        {"id": "drawdown", "group": "Safety", "label": f"Worst fall in the last year better than {r.max_drawdown:.0%}",
         "fn": lambda s: s["max_dd_1y"] > r.max_drawdown, "col": "max_dd_1y", "fmt": "pct",
         "why": "A share that lost two thirds of its value in a year is under stress."},
        {"id": "circuits", "group": "Safety", "label": f"Hit a price limit on at most {r.max_circuit_days} of the last 60 days",
         "fn": lambda s: s["circuit_days"] <= r.max_circuit_days, "col": "circuit_days", "fmt": "int",
         "why": "Stocks that keep hitting the 5/10/20% circuit are being pushed around, and you cannot sell in a lower circuit."},
        {"id": "mcap", "group": "Safety", "label": f"Market value at least ₹{r.min_mcap_cr:g} crore",
         "fn": lambda s: skip(s, s["mcap_cr"] >= r.min_mcap_cr), "col": "mcap_cr", "fmt": "crore",
         "why": "Very small companies are easy to manipulate."},
        {"id": "results", "group": "Quality", "label": "Company results available",
         "fn": (lambda s: ~none(s)) if not r.allow_no_results else (lambda s: s["price"].notna()), "col": "roe",
         "fmt": "pct", "why": "You cannot judge a business without its results."},
        {"id": "profit", "group": "Quality", "label": "Profitable last year",
         "fn": lambda s: skip(s, s["net_margin"] > 0), "col": "net_margin", "fmt": "pct",
         "why": "Loss makers burn money; many low-priced shares are exactly that."},
        {"id": "roe", "group": "Quality", "label": f"Return on equity at least {r.min_roe:.0%}",
         "fn": lambda s: skip(s, s["roe"] >= r.min_roe), "col": "roe", "fmt": "pct",
         "why": "The business must earn something on the owners' money."},
        {"id": "debt", "group": "Quality", "label": f"Debt no more than {r.max_de:g}x equity",
         "fn": lambda s: skip(s, s["de"] <= r.max_de), "col": "de", "fmt": "x",
         "why": "Heavy borrowing is what turns a bad year into a wipe-out."},
        {"id": "sales", "group": "Quality", "label": "Sales not shrinking",
         "fn": lambda s: skip(s, s["sales_growth"] >= r.min_sales_growth), "col": "sales_growth", "fmt": "pct",
         "why": "A business that is growing, or at least holding up."},
        {"id": "promoter", "group": "Quality", "label": f"Promoters hold at least {r.min_promoter:.0%}",
         "fn": lambda s: skip(s, s["promoter"] >= r.min_promoter), "col": "promoter", "fmt": "pct",
         "why": "Owners with their own money in the company have a reason to look after it."},
    ]


def evaluate(snap: pd.DataFrame, r: PennyRules) -> dict:
    """Which stocks pass each rule, in order, and the stocks that fail exactly one."""
    rules = rule_table(r)
    ok = {x["id"]: x["fn"](snap).fillna(False).astype(bool) for x in rules}
    funnel, alive = [{"label": "All NSE equities", "count": int(len(snap))}], pd.Series(True, index=snap.index)
    for x in rules:
        alive &= ok[x["id"]]
        funnel.append({"label": x["label"], "group": x["group"], "count": int(alive.sum())})
    passers = snap.index[alive]
    base = ok["price"] & ok["liquid"] & ok["history"]               # real candidates: cheap, tradable, with a record
    fails = pd.DataFrame({x["id"]: ~ok[x["id"]] for x in rules})
    nfail = fails.sum(axis=1)
    near = [{"ticker": t, "failed": next(x for x in rules if fails.loc[t, x["id"]]), "value": snap.loc[t]} for t in
            snap.index[base & (nfail == 1)]]
    return {"rules": rules, "ok": ok, "funnel": funnel, "passers": passers, "near": near}


# ----------------------------------------------------------------------------- score and explain
WEIGHTS = {"roe": 1.0, "roce": 0.5, "eps_growth": 0.7, "sales_growth": 0.7, "de": -0.7, "promoter": 0.4, "promoter_chg": 0.4,
           "ret_12m": 0.6, "vol_1y": -0.5, "liq": 0.3, "pe": -0.3}
CLIP = {"roe": (-0.1, 0.5), "roce": (-0.1, 0.6), "eps_growth": (-0.5, 1.0), "sales_growth": (-0.3, 0.8), "de": (0, 3),
        "promoter": (0, 0.75), "promoter_chg": (-0.05, 0.05), "ret_12m": (-0.5, 2.0), "vol_1y": (0.1, 1.5), "pe": (3, 100)}


def components(snap: pd.DataFrame) -> pd.DataFrame:
    c = pd.DataFrame(index=snap.index)
    for k, (lo, hi) in CLIP.items():
        c[k] = snap[k].clip(lo, hi)
    c["pe"] = np.log(c["pe"])
    c["liq"] = np.log(snap["value_cr"].clip(lower=0.01))
    return c


def long_run_score(snap: pd.DataFrame) -> pd.Series:
    """Weighted z-scores among the stocks that passed (missing parts count as average)."""
    c = components(snap)
    z = (c - c.mean()) / c.std(ddof=0).replace(0, 1)
    z = z.fillna(0.0).clip(-3, 3)
    return sum(WEIGHTS[k] * z[k] for k in WEIGHTS)


def explain(row: pd.Series, snap: pd.DataFrame, r: PennyRules, pledge: float | None) -> tuple[list[str], list[str]]:
    """(why it passes, red flags) in plain words."""
    pct = lambda v, d=0: f"{v * 100:.{d}f}%"
    top3 = lambda k, hi=True: snap[k].notna().sum() >= 4 and (row[k] >= snap[k].quantile(2 / 3) if hi else row[k] <= snap[k].quantile(1 / 3))
    why, flags = [], []
    if pd.notna(row["roe"]):
        why.append(f"Earns {pct(row['roe'])} on shareholders' money" + (" (among the best of the passers)" if top3("roe") else ""))
    if pd.notna(row["de"]):
        why.append("Almost no debt" if row["de"] < 0.1 else f"Debt is {row['de']:.2f}x equity" + (" (low)" if row["de"] < 0.5 else ""))
    if pd.notna(row["sales_growth"]) and pd.notna(row["eps_growth"]):
        why.append(f"Sales {row['sales_growth']:+.0%} and earnings per share {row['eps_growth']:+.0%} last year")
    elif pd.notna(row["sales_growth"]):
        why.append(f"Sales {row['sales_growth']:+.0%} last year")
    if pd.notna(row["promoter"]):
        extra = (f", up {row['promoter_chg'] * 100:.1f} points in a year" if (row["promoter_chg"] or 0) >= 0.005 else "")
        why.append(f"Promoters hold {pct(row['promoter'])}{extra}")
    if pd.notna(row["vs_sma200"]) and row["vs_sma200"] > 0:
        why.append(f"Trading {pct(row['vs_sma200'])} above its 200-day average")
    why.append(f"Trades about ₹{row['value_cr']:.1f} crore a day, so a small order will not move it")
    # flags
    if pledge is not None and pledge > 0.10:
        flags.append(f"{pct(pledge)} of promoter shares are pledged")
    if pd.notna(row["eps_growth"]) and row["eps_growth"] < -0.2:
        flags.append(f"Earnings per share fell {pct(-row['eps_growth'])} last year")
    if pd.notna(row["promoter_chg"]) and row["promoter_chg"] < -0.02:
        flags.append(f"Promoters sold {abs(row['promoter_chg']) * 100:.1f} points in a year")
    if pd.notna(row["pe"]) and row["pe"] > 40:
        flags.append(f"Expensive for its size: P/E {row['pe']:.0f}")
    if pd.notna(row["ret_3m"]) and row["ret_3m"] > 0.5:
        flags.append(f"Already up {pct(row['ret_3m'])} in 3 months")
    if row["circuit_days"] >= 2:
        flags.append(f"Hit a price limit on {int(row['circuit_days'])} of the last 60 days")
    if pd.notna(row.get("deliv_per")) and row["deliv_per"] < 25:
        flags.append(f"Only {row['deliv_per']:.0f}% of today's volume was delivered (mostly day traders)")
    if pd.notna(row["mcap_cr"]) and row["mcap_cr"] < 400:
        flags.append(f"Small company: ₹{row['mcap_cr']:.0f} crore")
    return why, flags


def _num(v):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else float(v)


# ----------------------------------------------------------------------------- the scan
def scan(rules: PennyRules, capital: float = 200_000, cache_dir=Path(".cache"), progress=None, offline: bool = False,
         today: date | None = None, bhav: pd.DataFrame | None = None) -> dict:
    progress = progress or (lambda m, f: None)
    cache_dir = Path(cache_dir)
    progress("Reading NSE's list of every share", 0.03)
    bhav = bhav if bhav is not None else fetch_bhavcopy(cache_dir, today, offline)
    names = {}
    try:
        eq = nse_equity(True)
        names = dict(zip(eq["SYMBOL"] + ".NS", eq["NAME OF COMPANY"]))
    except Exception:
        pass
    if bhav is not None:
        bhav = bhav[[is_company(t, names) for t in bhav.index]]
    universe = len(bhav) if bhav is not None else None
    if bhav is not None:
        cand = bhav[(bhav["close"] >= rules.price_floor) & (bhav["close"] <= rules.price_cap * 1.15)
                    & (bhav["turnover_lakh"].fillna(0) >= rules.min_lakh * 0.1)]
        tickers = list(cand.index)
        as_of = bhav.attrs.get("date")
    else:                                    # NSE's file is unreachable: use Yahoo's last month for every share
        progress("NSE's file is not reachable; checking prices one by one (slow)", 0.05)
        every = [t for t in get_universe("nse_all", True) if is_company(t, names)]
        month = load_universe(every, "1mo", "1d", offline, cache_dir, log=lambda *a: None,
                              progress=lambda m, f: progress(m, 0.05 + 0.2 * f))
        rows = {t: (float(d["Close"].iloc[-1]), float((d["Close"] * d["Volume"]).median() / 1e5)) for t, d in month.items() if len(d)}
        bhav = pd.DataFrame(rows, index=["close", "turnover_lakh"]).T
        bhav["deliv_per"] = np.nan
        universe = len(every)
        tickers = list(bhav[(bhav["close"] >= rules.price_floor) & (bhav["close"] <= rules.price_cap * 1.15)
                            & (bhav["turnover_lakh"] >= rules.min_lakh * 0.1)].index)
        as_of = None
    if not tickers:
        raise ValueError("No share matches the price and trading filters today")
    progress(f"Loading ten-year prices for {len(tickers)} cheap shares", 0.1)
    prices = load_universe(tickers + [BENCH], "10y", "1d", offline, cache_dir, log=lambda *a: None,
                           progress=lambda m, f: progress(m, 0.1 + 0.4 * f))
    bench = prices.pop(BENCH)["Close"] if BENCH in prices else None
    if bench is None:
        raise ValueError("Could not load the Nifty 50 prices")
    closes, values = aligned(prices)
    as_of = as_of or closes.index[-1].date()

    # company results for every share that could still qualify (cheap, tradable, long enough record)
    quick = pd.DataFrame({"price": closes.iloc[-1], "value_cr": values.iloc[-60:].median() / 1e7, "years": closes.notna().sum() / 252})
    need = list(quick.index[(quick["price"].between(rules.price_floor, rules.price_cap)) & (quick["value_cr"] * 100 >= rules.min_lakh)
                            & (quick["years"] >= rules.min_years)])
    progress(f"Company results for {len(need)} shares", 0.55)
    fund_data = F.fetch_many(need, cache_dir, offline, progress=lambda m, f: progress(m, 0.55 + 0.25 * f)) if need else {}
    progress("Applying the rules", 0.82)
    snap = snapshot_today(closes, values, bench, fund_data)
    snap["deliv_per"] = bhav["deliv_per"].reindex(snap.index)
    ev = evaluate(snap, rules)
    ev["funnel"][0]["count"] = int(universe or len(snap))
    passers = snap.loc[ev["passers"]].copy()
    ind, picks, dropped = industries(True), [], []
    if len(passers):
        passers["score"] = long_run_score(passers)
        passers = passers.sort_values("score", ascending=False)
        passers["score_pct"] = passers["score"].rank(pct=True)
    progress("Checking promoter pledges", 0.9)
    for t, row in passers.iterrows():
        if len(picks) >= rules.top:
            break
        pl = F.pledge(t, fund_data.get(t), cache_dir, offline) if row["price"] > 0 else None
        if pl is not None and pl > rules.max_pledge:
            dropped.append({"ticker": t, "name": names.get(t, ""), "why": f"{pl:.0%} of the promoters' shares are pledged"})
            continue
        why, flags = explain(row, passers, rules, pl)
        picks.append({"ticker": t, "name": names.get(t, ""), "industry": ind.get(t, ""), "price": float(row["price"]),
                      "score": float(row["score"]), "score_pct": float(row["score_pct"]), "pledge": pl, "why": why, "flags": flags,
                      **{k: _num(row[k]) for k in ("roe", "roce", "de", "pe", "pb", "eps_growth", "sales_growth", "net_margin",
                                                    "div_yield", "mcap_cr", "promoter", "promoter_chg", "ret_3m", "ret_6m", "ret_12m",
                                                    "vs_sma200", "from_high", "vol_1y", "max_dd_1y", "value_cr", "deliv_per", "years")},
                      "circuit_days": int(row["circuit_days"])})
    # a small equal slice of the bucket for each pick, never more than 5% of a day's trading in it
    bucket = capital * rules.bucket_pct / 100
    each = bucket / max(len(picks), 1)
    for p in picks:
        amount = min(each, 0.05 * (p["value_cr"] or 0) * 1e7)
        p["amount"] = amount
        p["shares"] = int(amount // p["price"])
        p["amount"] = p["shares"] * p["price"]
    near = []
    for x in ev["near"]:
        v = x["value"]
        col = x["failed"]["col"]
        near.append({"ticker": x["ticker"], "name": names.get(x["ticker"], ""), "price": float(v["price"]),
                     "value_cr": _num(v["value_cr"]), "rule": x["failed"]["label"], "actual": _num(v[col]), "fmt": x["failed"]["fmt"],
                     "roe": _num(v["roe"])})
    near.sort(key=lambda d: -(d["value_cr"] or 0))
    progress("Done", 1.0)
    return {"as_of": str(as_of), "generated": datetime.now().isoformat(timespec="minutes"), "capital": capital,
            "rules": asdict(rules), "universe": universe, "candidates": len(tickers), "funnel": ev["funnel"],
            "passers": int(len(passers)), "picks": picks, "dropped": dropped, "near_misses": near[:12],
            "rule_list": [{k: x[k] for k in ("id", "group", "label", "why")} for x in ev["rules"]],
            "bucket": bucket, "per_stock": each, "source": "NSE daily file" if as_of else "Yahoo"}


# ----------------------------------------------------------------------------- history test
def price_score(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """A price-only version of the score for the history test: steady, rising and tradable, row by row."""
    def z(df):
        return df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1).replace(0, np.nan), axis=0).clip(-3, 3).fillna(0)
    liq = np.log(frames["value_cr"].clip(lower=0.01))
    return (z(frames["ret_12m"].clip(-0.5, 2)) + 0.5 * z(frames["ret_6m"].clip(-0.5, 2)) - 0.5 * z(frames["vol_1y"].clip(0.1, 1.5))
            + 0.3 * z(liq)).fillna(0)


def outcomes(closes: pd.DataFrame, allowed, dates, years: int = 3) -> dict | None:
    """What happened over the next `years` years to every cheap, tradable share on each year-end: the spread
    of results. Only shares that still trade are in the data, so the true picture is worse."""
    rows = []
    for d in dates:
        end = d + pd.DateOffset(years=years)
        if end > closes.index[-1] or len(closes.loc[:d]) < 260:
            continue
        ts = allowed(d)
        r = (closes.loc[:end].iloc[-1][ts] / closes.loc[:d].iloc[-1][ts] - 1).dropna()
        if len(r):
            rows.append(r)
    if not rows:
        return None
    r = pd.concat(rows)
    return {"years": years, "n": int(len(r)), "dates": len(rows), "median": float(r.median()), "share_down": float((r < 0).mean()),
            "share_lost_half": float((r < -0.5).mean()), "share_doubled": float((r > 1.0).mean()),
            "share_tripled": float((r > 2.0).mean()), "p10": float(r.quantile(0.1)), "p90": float(r.quantile(0.9))}


def backtest(rules: PennyRules, capital: float = 200_000, cache_dir=Path(".cache"), progress=None, offline: bool = False,
             per_order: float = 0.0) -> dict:
    """Yearly rebalanced, equal-weight top picks among the penny stocks of each date, against every penny stock
    of that date and the Nifty Smallcap 250. Price and liquidity rules only (company results are too short)."""
    progress = progress or (lambda m, f: None)
    cache_dir = Path(cache_dir)
    progress("Loading every NSE share", 0.02)
    try:
        eq = nse_equity(True)
        names = dict(zip(eq["SYMBOL"] + ".NS", eq["NAME OF COMPANY"]))
    except Exception:
        names = {}
    tickers = [t for t in get_universe("nse_all", True) if is_company(t, names)]
    prices = load_universe(tickers + [BENCH], "10y", "1d", offline, cache_dir, log=lambda *a: None,
                           progress=lambda m, f: progress(m, 0.02 + 0.4 * f))
    nifty = prices.pop(BENCH)["Close"]
    closes = pd.DataFrame({t: d["Close"] for t, d in prices.items()}).sort_index()
    values = pd.DataFrame({t: d["Close"] * d["Volume"] for t, d in prices.items()}).reindex(closes.index)
    med = values.rolling(60, min_periods=40).median()
    ever = ((closes <= rules.price_cap) & (closes >= rules.price_floor) & (med >= rules.min_lakh * 1e5)).any()
    closes, values = closes.loc[:, ever], values.loc[:, ever]
    sm_sym = SMALLCAP
    if not offline:
        refresh_many([sm_sym], "10y", "1d", cache_dir)
    try:
        bench = A.total_return(sm_sym, read_cached(_cache_path(cache_dir, sm_sym, "10y", "1d"))["Close"])
        bench_label = "Nifty Smallcap 250"
    except Exception:
        bench, bench_label = A.total_return(BENCH, nifty), "Nifty 50"
    progress("Measuring every share on every day", 0.45)
    frames = M.metric_frames(closes, values, bench)
    frames["pscore"] = price_score(frames)
    cap_med = med.loc[:, ever]

    def allowed(d):
        px = closes.loc[:d].iloc[-1]
        ok = (px >= rules.price_floor) & (px <= rules.price_cap) & (cap_med.loc[:d].iloc[-1] >= rules.min_lakh * 1e5)
        return ok.index[ok.fillna(False)]
    filters = [{"metric": "vs_sma200", "op": ">", "value": 0}] if rules.uptrend else []
    filters += [{"metric": "ret_3m", "op": "<", "value": rules.max_ret_3m}, {"metric": "ret_12m", "op": "<", "value": rules.max_ret_12m},
                {"metric": "max_dd_1y", "op": ">", "value": rules.max_drawdown}]
    sc = M.Screen(filters=filters, rank_by="pscore", descending=True, top=rules.top, rebalance="Y")
    bt = M.backtest(closes, frames, bench, sc, lambda m, f: progress(m, 0.5 + 0.45 * min(max((f - 0.4) / 0.5, 0), 1)), values,
                    capital, per_order, allowed_fn=allowed)
    bt.pop("daily", None)
    progress("Done", 1.0)
    return {"backtest": bt, "benchmark": {"label": bench_label}, "rules": asdict(rules), "stocks_seen": int(closes.shape[1]),
            "outcomes": outcomes(closes, allowed, M._rebalance_dates(closes.index, "Y")),
            "generated": datetime.now().isoformat(timespec="minutes")}
