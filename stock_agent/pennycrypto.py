"""Penny crypto for the long run: an automatic screen for low-priced coins that are more than a pump.

Most small coins go nowhere and many go to zero. This screen does not hunt for the next 100x. It removes the
traps (stablecoins and wrapped tokens, coins nobody trades, ones being pumped, ones with huge token unlocks
ahead, very young projects) and ranks what is left on strength against Bitcoin, steadiness and supply health,
so a small long-term bucket is spread over the better ones.

Data: CoinPaprika's list of every coin in one call (price, market value, volume, supply, first-trade date), or
CoinGecko's if that is down; then Yahoo's daily history for the few coins that pass, to check the trend, the
fall from the 1-year high and the strength against Bitcoin. A coin whose Yahoo price does not match is ignored.

  1. The coin     not a stablecoin, wrapped, staked or gold token; price under $1; market value $50 million to
                  $3 billion; among the top 400 by size
  2. Trading      at least $2 million traded a day, between 0.5% and 80% of its market value (less is
                  untradable, more is mostly speculation or wash trading)
  3. Safety       at least 2 years old, fully diluted value no more than 3x the market value (limits unlock
                  risk), in an uptrend, not up 100% in 30 days or 200% in 90 days, within 85% of its 1-year high

There is no history test here: coin lists only contain coins that survived, so any test of them would flatter
the group heavily.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .data import _cache_path, read_cached, refresh_many

PAPRIKA = "https://api.coinpaprika.com/v1/tickers?quotes=USD"
GECKO = ("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page={p}"
         "&price_change_percentage=7d,30d,1y")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/json"}
STABLE = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD", "USDD", "USDS", "GUSD", "FRAX", "LUSD", "BUSD", "USDP", "USDG", "RLUSD",
          "USD1", "EURC", "EURT", "XAUT", "PAXG", "WBTC", "WETH", "STETH", "WSTETH", "CBBTC", "WEETH", "RETH", "BTCB", "USDX"}
NOT_A_COIN = re.compile(r"\b(usd|usdt|usdc|dollar|stablecoin|wrapped|staked|bridged|tokenized|tether|gold|silver|peg|pegged|synthetic|"
                        r"liquid staking|restaked)\b", re.I)
BUY_NOTE = ("Check that your exchange lists the coin before you plan around it: many small coins are not on Indian exchanges. "
            "In India crypto gains are taxed at a flat 30% with 1% TDS on each sale, and losses cannot be set off against other gains.")


@dataclass
class CryptoRules:
    price_cap: float = 1.0               # US dollars
    min_mcap: float = 5e7
    max_mcap: float = 3e9
    max_rank: int = 400
    min_volume: float = 2e6              # dollars traded in 24 hours
    min_turnover: float = 0.005          # 24-hour volume / market value
    max_turnover: float = 0.8
    min_years: float = 2.0
    max_fdv_ratio: float = 3.0           # (max or total supply) / circulating supply
    uptrend: bool = True
    max_30d: float = 1.0                 # up more than 100% in 30 days: being pumped
    max_90d: float = 2.0                 # or more than 200% in 90 days
    max_dd_52w: float = -0.85            # price must be within 85% of its 1-year high
    allow_no_history: bool = False
    top: int = 8
    bucket_pct: float = 5.0


def rules_from(d: dict | None) -> CryptoRules:
    base = CryptoRules()
    for k, v in (d or {}).items():
        if hasattr(base, k) and v is not None:
            setattr(base, k, type(getattr(base, k))(v))
    base.top = int(min(max(base.top, 3), 20))
    base.bucket_pct = float(min(max(base.bucket_pct, 0.5), 20))
    base.price_cap = float(min(max(base.price_cap, 0.01), 50))
    return base


# ----------------------------------------------------------------------------- the coin list
def _age_years(first: str | None, today: date) -> float | None:
    try:
        return (today - date.fromisoformat(str(first)[:10])).days / 365.25
    except (TypeError, ValueError):
        return None


def from_paprika(rows: list[dict], today: date | None = None) -> pd.DataFrame:
    today = today or date.today()
    out = []
    for c in rows:
        q = (c.get("quotes") or {}).get("USD") or {}
        if not q.get("price") or not q.get("market_cap"):
            continue
        out.append({"id": c["id"], "symbol": str(c["symbol"]).upper(), "name": c["name"], "rank": c.get("rank") or 10**6,
                    "price": q["price"], "mcap": q["market_cap"], "volume": q.get("volume_24h") or 0.0,
                    "total_supply": c.get("total_supply") or 0, "max_supply": c.get("max_supply") or 0,
                    "years": _age_years(c.get("first_data_at"), today), "pct7": q.get("percent_change_7d"),
                    "from_ath": (q.get("percent_from_price_ath") or 0) / 100 if q.get("ath_price") else None})
    return pd.DataFrame(out)


def from_gecko(pages: list[list[dict]]) -> pd.DataFrame:
    out = []
    for c in (x for p in pages for x in p):
        if not c.get("current_price") or not c.get("market_cap"):
            continue
        out.append({"id": c["id"], "symbol": str(c["symbol"]).upper(), "name": c["name"], "rank": c.get("market_cap_rank") or 10**6,
                    "price": c["current_price"], "mcap": c["market_cap"], "volume": c.get("total_volume") or 0.0,
                    "total_supply": c.get("total_supply") or 0, "max_supply": c.get("max_supply") or 0, "years": None,
                    "pct7": c.get("price_change_percentage_7d_in_currency"),
                    "from_ath": (c.get("ath_change_percentage") or 0) / 100 if c.get("ath") else None,
                    "circ": c.get("circulating_supply")})
    return pd.DataFrame(out)


def fetch_coins(today: date | None = None) -> tuple[pd.DataFrame, str]:
    errors = []
    try:
        r = requests.get(PAPRIKA, headers=UA, timeout=60)
        r.raise_for_status()
        df = from_paprika(r.json(), today)
        if len(df) > 100:
            return df, "CoinPaprika"
    except Exception as exc:
        errors.append(f"CoinPaprika: {exc}")
    try:
        pages = []
        for p in (1, 2):
            r = requests.get(GECKO.format(p=p), headers=UA, timeout=60)
            r.raise_for_status()
            pages.append(r.json())
        df = from_gecko(pages)
        if len(df) > 100:
            return df, "CoinGecko"
    except Exception as exc:
        errors.append(f"CoinGecko: {exc}")
    raise ValueError("Could not reach a coin list (" + "; ".join(errors) + ")")


def is_real_coin(row: pd.Series) -> bool:
    """Not a stablecoin, a wrapped or staked copy of another asset, or a gold token."""
    if row["symbol"] in STABLE or NOT_A_COIN.search(str(row["name"])):
        return False
    return not (0.97 <= row["price"] <= 1.03 and abs(row.get("pct7") or 0) < 1.0)     # sits at $1: a peg


# ----------------------------------------------------------------------------- history
def history_metrics(s: pd.Series, btc: pd.Series | None) -> dict:
    s = s.dropna()
    n = len(s)
    if n < 30:
        return {}
    last = float(s.iloc[-1])
    at = lambda days: float(s.iloc[-1 - days]) if n > days else None
    ret = lambda days: (last / at(days) - 1) if at(days) else None
    out = {"hist_years": n / 365.25, "ret_30d": ret(30), "ret_90d": ret(90), "ret_180d": ret(180), "ret_365d": ret(365),
           "vs_sma200": (last / float(s.iloc[-200:].mean()) - 1) if n >= 200 else None,
           "dd_52w": last / float(s.iloc[-365:].max()) - 1,
           "max_dd": float((s / s.cummax() - 1).min()),
           "vol": float(s.pct_change().iloc[-90:].std() * math.sqrt(365))}
    if btc is not None and len(btc.dropna()) > 181 and out["ret_180d"] is not None:
        b = btc.dropna()
        out["rs_180d"] = out["ret_180d"] - float(b.iloc[-1] / b.iloc[-181] - 1)
    return out


def load_history(symbols: list[str], cache_dir: Path, offline: bool) -> dict[str, pd.Series]:
    wanted = sorted(set(symbols) | {"BTC-USD"})
    if not offline:
        refresh_many(wanted, "10y", "1d", cache_dir)
    out = {}
    for sym in wanted:
        path = _cache_path(cache_dir, sym, "10y", "1d")
        if path.exists():
            try:
                out[sym] = read_cached(path)["Close"]
            except Exception:
                continue
    return out


# ----------------------------------------------------------------------------- rules
def rule_table(r: CryptoRules) -> list[dict]:
    # a coin without a price record fails the "history" rule alone; the trend rules wait for a record to exist
    hist = lambda s, ok: ok | s["hist_years"].isna()
    return [
        {"id": "coin", "group": "The coin", "label": "A real coin (not a stablecoin, wrapped, staked or gold token)",
         "fn": lambda s: s["real"], "col": "price", "fmt": "usd", "why": "Wrapped and pegged tokens just copy another asset."},
        {"id": "price", "group": "The coin", "label": f"Price under ${r.price_cap:g}", "fn": lambda s: s["price"] <= r.price_cap,
         "col": "price", "fmt": "usd", "why": "The definition of a penny coin for this screen."},
        {"id": "size", "group": "The coin", "label": f"Market value ${r.min_mcap / 1e6:g} million to ${r.max_mcap / 1e9:g} billion, in the top {r.max_rank}",
         "fn": lambda s: (s["mcap"] >= r.min_mcap) & (s["mcap"] <= r.max_mcap) & (s["rank"] <= r.max_rank), "col": "mcap", "fmt": "usdm",
         "why": "Smaller coins are easy to manipulate; larger ones are not penny coins."},
        {"id": "volume", "group": "Trading", "label": f"At least ${r.min_volume / 1e6:g} million traded a day", "fn": lambda s: s["volume"] >= r.min_volume,
         "col": "volume", "fmt": "usdm", "why": "If you cannot sell, a coin is worth nothing."},
        {"id": "turnover", "group": "Trading", "label": f"Daily trading {r.min_turnover:.1%} to {r.max_turnover:.0%} of its market value",
         "fn": lambda s: (s["turnover"] >= r.min_turnover) & (s["turnover"] <= r.max_turnover), "col": "turnover", "fmt": "pct",
         "why": "Almost no trading means no market; far more than the whole coin changing hands is speculation or wash trading."},
        {"id": "age", "group": "Safety", "label": f"At least {r.min_years:g} years old",
         "fn": lambda s: s["years_all"] >= r.min_years, "col": "years_all", "fmt": "years",
         "why": "Most coins that die do so in their first two years."},
        {"id": "unlocks", "group": "Safety", "label": f"Fully diluted value at most {r.max_fdv_ratio:g}x the market value",
         "fn": lambda s: s["fdv_ratio"].isna() | (s["fdv_ratio"] <= r.max_fdv_ratio), "col": "fdv_ratio", "fmt": "x",
         "why": "If most coins are still to be released, selling by early holders can hold the price down for years."},
        {"id": "history", "group": "Safety", "label": "Price history available to check the trend",
         "fn": (lambda s: s["hist_years"].notna()) if not r.allow_no_history else (lambda s: s["price"].notna()), "col": "hist_years", "fmt": "years",
         "why": "Without a price record the trend and the fall from the high cannot be checked."},
        {"id": "trend", "group": "Safety", "label": "Price above its 200-day average" if r.uptrend else "Trend not checked",
         "fn": (lambda s: hist(s, s["vs_sma200"] > 0)) if r.uptrend else (lambda s: s["price"].notna()), "col": "vs_sma200", "fmt": "pct",
         "why": "Buying after the market has started to agree avoids coins that are still falling."},
        {"id": "pump", "group": "Safety", "label": f"Not up more than {r.max_30d:.0%} in 30 days or {r.max_90d:.0%} in 90 days",
         "fn": lambda s: hist(s, (s["ret_30d"] <= r.max_30d) & (s["ret_90d"] <= r.max_90d)), "col": "ret_30d", "fmt": "pct",
         "why": "A coin that has just multiplied is usually followed by a crash."},
        {"id": "drawdown", "group": "Safety", "label": f"Within {-r.max_dd_52w:.0%} of its 1-year high",
         "fn": lambda s: hist(s, s["dd_52w"] >= r.max_dd_52w), "col": "dd_52w", "fmt": "pct",
         "why": "A coin that lost almost everything in a year is dying."},
    ]


def evaluate(snap: pd.DataFrame, r: CryptoRules) -> dict:
    rules = rule_table(r)
    ok = {x["id"]: x["fn"](snap).fillna(False).astype(bool) for x in rules}
    funnel, alive = [], pd.Series(True, index=snap.index)
    for x in rules:
        alive &= ok[x["id"]]
        funnel.append({"label": x["label"], "group": x["group"], "count": int(alive.sum())})
    fails = pd.DataFrame({x["id"]: ~ok[x["id"]] for x in rules})
    base = ok["coin"] & ok["price"] & ok["size"] & ok["volume"]
    near = [{"id": t, "failed": next(x for x in rules if fails.loc[t, x["id"]]), "value": snap.loc[t]}
            for t in snap.index[base & (fails.sum(axis=1) == 1)]]
    return {"rules": rules, "funnel": funnel, "passers": snap.index[alive], "near": near}


WEIGHTS = {"rs_180d": 1.0, "ret_90d": 0.6, "vol": -0.5, "dd_52w": 0.3, "fdv_log": -0.5, "turn_log": 0.3, "years_log": 0.4}
CLIP = {"rs_180d": (-1.0, 3.0), "ret_90d": (-0.8, 3.0), "vol": (0.2, 3.0), "dd_52w": (-0.9, 0.0)}


def long_run_score(snap: pd.DataFrame) -> pd.Series:
    c = pd.DataFrame(index=snap.index)
    for k, (lo, hi) in CLIP.items():
        c[k] = snap[k].clip(lo, hi)
    c["fdv_log"] = np.log(snap["fdv_ratio"].clip(lower=1.0))
    c["turn_log"] = np.log(snap["turnover"].clip(lower=0.001))
    c["years_log"] = np.log(snap["years_all"].clip(lower=0.5))
    z = ((c - c.mean()) / c.std(ddof=0).replace(0, 1)).fillna(0.0).clip(-3, 3)
    return sum(WEIGHTS[k] * z[k] for k in WEIGHTS)


def explain(row: pd.Series) -> tuple[list[str], list[str]]:
    pct = lambda v: f"{v * 100:.0f}%"
    why, flags = [], []
    if pd.notna(row.get("rs_180d")):
        why.append(f"{'Beat' if row['rs_180d'] > 0 else 'Lagged'} Bitcoin by {abs(row['rs_180d']) * 100:.0f} points over 6 months")
    if pd.notna(row.get("vs_sma200")) and row["vs_sma200"] > 0:
        why.append(f"Trading {pct(row['vs_sma200'])} above its 200-day average")
    if pd.notna(row.get("fdv_ratio")):
        why.append("Nearly all coins are already in circulation" if row["fdv_ratio"] <= 1.15
                   else f"{(1 / row['fdv_ratio']) * 100:.0f}% of the eventual supply is in circulation")
    why.append(f"${row['volume'] / 1e6:.1f} million traded a day ({row['turnover']:.1%} of its value)")
    why.append(f"{row['years_all']:.1f} years of trading" if pd.notna(row["years_all"]) else "Age unknown")
    if pd.notna(row["fdv_ratio"]) and row["fdv_ratio"] > 2:
        flags.append(f"{(1 - 1 / row['fdv_ratio']) * 100:.0f}% of the coins are still to be released: selling pressure ahead")
    if not (row.get("max_supply") or 0):
        flags.append("No supply cap: new coins can keep being created")
    if pd.notna(row.get("ret_30d")) and row["ret_30d"] > 0.6:
        flags.append(f"Already up {pct(row['ret_30d'])} in 30 days")
    if row["turnover"] > 0.3:
        flags.append(f"Heavy speculation: {row['turnover']:.0%} of its value trades every day")
    if pd.notna(row.get("vol")) and row["vol"] > 1.5:
        flags.append(f"Very volatile: swings about {row['vol']:.0%} a year")
    if pd.notna(row.get("from_ath")) and row["from_ath"] < -0.9:
        flags.append(f"{pct(-row['from_ath'])} below its all-time high")
    if row["years_all"] < 3:
        flags.append("Under 3 years old")
    if row["rank"] > 250:
        flags.append(f"Rank {int(row['rank'])}: thin market")
    return why, flags


def _num(v):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else float(v)


# ----------------------------------------------------------------------------- the scan
def scan(rules: CryptoRules, capital: float = 200_000, cache_dir=Path(".cache"), progress=None, offline: bool = False,
         today: date | None = None, coins: pd.DataFrame | None = None, usd_inr: float | None = None) -> dict:
    progress = progress or (lambda m, f: None)
    cache_dir = Path(cache_dir)
    today = today or date.today()
    progress("Reading the list of every coin", 0.05)
    source = "given"
    if coins is None:
        coins, source = fetch_coins(today)
    coins = coins.copy()
    coins["real"] = coins.apply(is_real_coin, axis=1)
    coins["turnover"] = coins["volume"] / coins["mcap"]
    circ = coins["circ"] if "circ" in coins else coins["mcap"] / coins["price"]
    full = coins[["total_supply", "max_supply"]].max(axis=1)
    coins["fdv_ratio"] = (full / circ).where(full > 0)
    coins["years_all"] = coins["years"]
    # coins that could still qualify on the list's own numbers: these get a price history
    first = (coins["real"] & (coins["price"] <= rules.price_cap) & (coins["mcap"] >= rules.min_mcap) & (coins["mcap"] <= rules.max_mcap)
             & (coins["rank"] <= rules.max_rank) & (coins["volume"] >= rules.min_volume))
    cand = coins[first].sort_values("rank").drop_duplicates("symbol")
    progress(f"Price history for {len(cand)} coins", 0.25)
    syms = [f"{s}-USD" for s in cand["symbol"]]
    hist = load_history(syms, cache_dir, offline) if len(cand) else {}
    btc = hist.get("BTC-USD")
    cols = ["hist_years", "ret_30d", "ret_90d", "ret_180d", "ret_365d", "vs_sma200", "dd_52w", "max_dd", "vol", "rs_180d"]
    for c in cols:
        coins[c] = np.nan
    coins = coins.set_index("id")
    for _, row in cand.iterrows():
        s = hist.get(f"{row['symbol']}-USD")
        if s is None or len(s.dropna()) < 30:
            continue
        if abs(float(s.dropna().iloc[-1]) / row["price"] - 1) > 0.25:      # a different coin with the same ticker
            continue
        for k, v in history_metrics(s, btc).items():
            coins.loc[row["id"], k] = v
    coins["years_all"] = coins[["years_all", "hist_years"]].max(axis=1)
    progress("Applying the rules", 0.8)
    ev = evaluate(coins, rules)
    ev["funnel"].insert(0, {"label": "Coins listed", "group": "", "count": int(len(coins))})
    passers = coins.loc[ev["passers"]].copy()
    picks = []
    if len(passers):
        passers["score"] = long_run_score(passers)
        passers = passers.sort_values("score", ascending=False)
        passers["score_pct"] = passers["score"].rank(pct=True)
    if usd_inr is None:
        try:
            refresh_many(["USDINR=X"], "1mo", "1d", cache_dir) if not offline else None
            usd_inr = float(read_cached(_cache_path(cache_dir, "USDINR=X", "1mo", "1d"))["Close"].iloc[-1])
        except Exception:
            usd_inr = 88.0
    bucket = capital * rules.bucket_pct / 100
    chosen = passers.head(rules.top)
    each = bucket / max(len(chosen), 1)
    for cid, row in chosen.iterrows():
        why, flags = explain(row)
        units = each / (row["price"] * usd_inr)
        picks.append({"id": cid, "ticker": f"{row['symbol']}-USD", "symbol": row["symbol"], "name": row["name"], "price": float(row["price"]),
                      "rank": int(row["rank"]), "score": float(row["score"]), "score_pct": float(row["score_pct"]), "why": why,
                      "flags": flags, "amount": each, "units": units,
                      **{k: _num(row[k]) for k in ("mcap", "volume", "turnover", "years_all", "fdv_ratio", "pct7", "from_ath", "ret_30d", "ret_90d",
                                                    "ret_180d", "ret_365d", "vs_sma200", "dd_52w", "max_dd", "vol", "rs_180d")},
                      "max_supply": _num(row["max_supply"]), "history": bool(pd.notna(row["hist_years"]))})
    near = []
    for x in ev["near"]:
        v, col = x["value"], x["failed"]["col"]
        near.append({"ticker": f"{v['symbol']}-USD", "name": v["name"], "price": float(v["price"]), "mcap": _num(v["mcap"]),
                     "rule": x["failed"]["label"], "actual": _num(v[col]), "fmt": x["failed"]["fmt"]})
    near.sort(key=lambda d: -(d["mcap"] or 0))
    progress("Done", 1.0)
    return {"as_of": str(today), "generated": datetime.now().isoformat(timespec="minutes"), "capital": capital, "rules": asdict(rules),
            "usd_inr": usd_inr, "funnel": ev["funnel"], "passers": int(len(passers)), "picks": picks, "near_misses": near[:12],
            "rule_list": [{k: x[k] for k in ("id", "group", "label", "why")} for x in ev["rules"]], "bucket": bucket,
            "per_coin": each, "source": source, "note": BUY_NOTE}
