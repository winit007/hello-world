"""Robust backtest: modern checks of whether the candlestick signals have an edge, and a filter that tries
to raise the win rate, judged only on years it never saw.

Methods (from the quant-research literature, mainly M. López de Prado, "Advances in Financial ML", and
Bailey, Borwein, López de Prado & Zhu, "The Probability of Backtest Overfitting"):

  walk-forward   Each test year, the rules are chosen again using only earlier years (patterns with a
                 positive average option return over at least 30 trades), then traded in that year.
  purge/embargo  Training trades whose holding period runs into the test year are dropped, plus a
                 10-day embargo, so no future information leaks into the choice.
  meta-label     A second model (L2 logistic regression, numpy) learns from conditions at the signal
                 - trend, momentum, volatility, RSI, volume, distance from the 52-week high, market regime,
                 pattern - which signals tend to win. Walk-forward like above; its decision threshold is
                 picked on the training years. Out-of-sample calibration shows whether its probabilities
                 mean what they say.
  PBO            Probability of backtest overfitting by combinatorially symmetric cross-validation over
                 the patterns: how often the pattern that looked best in half the history ranks in the
                 bottom half in the other half.
  FDR            Benjamini-Hochberg false discovery control on "this pattern wins more often than a random
                 day": how many patterns survive once luck from testing many is accounted for.
  bootstrap      Month-block bootstrap of out-of-sample trade returns: a 90% range for the average return
                 and the chance it is above zero.

Nothing here is tuned on the test years. The result is reported as is, good or bad.
"""
from __future__ import annotations

import itertools
import math
from datetime import date

import numpy as np
import pandas as pd

from .patterns import PATTERNS
from .tradetest import Strategy, load_strategy, market_regime, trade_outcomes

FEATURES = ["trend50", "trend200", "mom20", "mom60", "vol20", "atr_pct", "rsi14", "volume_ratio", "from_high",
            "market_up", "market_mom20"]
PATTERN_NAMES = [p.name for p in PATTERNS]
EMBARGO_DAYS = 10
MIN_TRADES = 30


# --------------------------------------------------------------------------- features at the signal bar
def _rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.where(dn > 0))
    return rsi.mask((dn == 0) & up.notna(), np.where(up > 0, 100.0, 50.0))   # no down days: RSI is 100


def stock_features(df: pd.DataFrame, index_df: pd.DataFrame | None) -> pd.DataFrame:
    """Signal-time conditions, using data up to and including that day only."""
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    f = pd.DataFrame(index=df.index)
    f["trend50"] = c / c.rolling(50, min_periods=40).mean() - 1
    f["trend200"] = c / c.rolling(200, min_periods=150).mean() - 1
    f["mom20"] = c.pct_change(20)
    f["mom60"] = c.pct_change(60)
    f["vol20"] = c.pct_change().rolling(20).std() * math.sqrt(252)
    f["atr_pct"] = tr.rolling(14).mean() / c
    f["rsi14"] = (_rsi(c) - 50) / 50
    f["volume_ratio"] = np.log((v / v.rolling(20).mean()).replace(0, np.nan))
    f["from_high"] = c / c.rolling(252, min_periods=120).max() - 1
    if index_df is not None and len(index_df):
        ic = index_df["Close"].reindex(df.index, method="ffill")
        f["market_up"] = (ic > ic.rolling(200, min_periods=150).mean()).astype(float)
        f["market_mom20"] = ic.pct_change(20)
    else:
        f["market_up"], f["market_mom20"] = 0.5, 0.0
    return f


def build_dataset(prices: dict[str, pd.DataFrame], index_df: pd.DataFrame | None, st: Strategy | None = None,
                  progress=None, instrument: str = "option", cost: float = 0.0) -> pd.DataFrame:
    """One row per signal trade: outcome plus the conditions when it fired, signed by direction
    (a falling market is 'aligned' for a bearish signal)."""
    st = st or load_strategy()
    regime = market_regime(index_df)
    parts = []
    for k, (t, df) in enumerate(prices.items()):
        o = trade_outcomes(df, t, strategy=st, regime=regime, baseline_step=0, instrument=instrument, cost=cost)
        o = o[~o["skipped"]]
        if o.empty:
            continue
        f = stock_features(df, index_df)
        x = f.reindex(o["signal"]).reset_index(drop=True)
        o = pd.concat([o.reset_index(drop=True), x], axis=1).assign(ticker=t)
        parts.append(o)
        if progress:
            progress(f"Signals and conditions: {t}", 0.05 + 0.45 * (k + 1) / len(prices))
    data = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if data.empty:
        return data
    sign = np.where(data["direction"] == "bullish", 1.0, -1.0)
    for col in ("trend50", "trend200", "mom20", "mom60", "rsi14", "from_high", "market_mom20"):
        data[col] = data[col] * sign
    data["market_up"] = np.where(sign > 0, data["market_up"], 1 - data["market_up"])
    data["win"] = (data["ret"] > 0).astype(int)
    return data.dropna(subset=FEATURES).sort_values("signal").reset_index(drop=True)


# --------------------------------------------------------------------------- the meta-label model
def _design(d: pd.DataFrame, mu=None, sd=None):
    x = d[FEATURES].to_numpy(float)
    if mu is None:
        mu, sd = x.mean(0), x.std(0) + 1e-9
    x = np.clip((x - mu) / sd, -5, 5)
    pats = np.array([[1.0 if p == q else 0.0 for q in PATTERN_NAMES] for p in d["pattern"]]) if len(d) else np.zeros((0, len(PATTERN_NAMES)))
    bull = (d["direction"] == "bullish").to_numpy(float)[:, None]
    return np.hstack([np.ones((len(d), 1)), x, pats, bull]), mu, sd


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 400) -> np.ndarray:
    """L2-regularised logistic regression by Newton's method (intercept not penalised)."""
    w = np.zeros(X.shape[1])
    reg = np.full(X.shape[1], l2)
    reg[0] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
        g = X.T @ (p - y) + reg * w
        H = (X * (p * (1 - p))[:, None]).T @ X + np.diag(reg + 1e-9)
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-7:
            break
    return w


def predict(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))


def _summary(d: pd.DataFrame) -> dict:
    if d.empty:
        return {"trades": 0, "win_rate": None, "avg_ret": None}
    return {"trades": int(len(d)), "win_rate": float(d["win"].mean()), "avg_ret": float(d["ret"].mean())}


def _train_window(data: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    """Purged training set: trades that closed before the test period, minus an embargo."""
    return data[data["exit"] < start - pd.Timedelta(days=EMBARGO_DAYS)]


def walk_forward(data: pd.DataFrame, first_test_year: int | None = None, l2: float = 1.0) -> dict:
    years = sorted(data["signal"].dt.year.unique())
    first = first_test_year or (years[0] + 4 if len(years) > 5 else years[-1])
    per_year, oos = [], []
    for y in [yy for yy in years if yy >= first]:
        start, end = pd.Timestamp(y, 1, 1), pd.Timestamp(y + 1, 1, 1)
        train, test = _train_window(data, start), data[(data["signal"] >= start) & (data["signal"] < end)]
        if len(train) < 500 or test.empty:
            continue
        # (a) pattern selection, re-done each year
        g = train.groupby(["pattern", "direction"])["ret"].agg(["mean", "size"])
        keep = set(g[(g["mean"] > 0) & (g["size"] >= MIN_TRADES)].index)
        sel = test[[k in keep for k in zip(test["pattern"], test["direction"])]]
        # (b) meta-label model
        Xtr, mu, sd = _design(train)
        w = fit_logistic(Xtr, train["win"].to_numpy(float), l2)
        ptr = predict(w, Xtr)
        thr = _pick_threshold(train.assign(p=ptr))
        Xte, _, _ = _design(test, mu, sd)
        pte = predict(w, Xte)
        t = test.assign(p=pte, year=y)
        oos.append(t)
        per_year.append({"year": int(y), "all": _summary(test), "selected_patterns": _summary(sel),
                         "patterns_kept": len(keep), "meta": _summary(t[t["p"] >= thr]), "threshold": float(thr)})
    oos_df = pd.concat(oos, ignore_index=True) if oos else pd.DataFrame()
    thr_by_year = {r["year"]: r["threshold"] for r in per_year}
    kept = oos_df[oos_df["p"] >= oos_df["year"].map(thr_by_year)] if len(oos_df) else oos_df
    return {"per_year": per_year, "all": _summary(oos_df), "meta": _summary(kept),
            "selected": _combine([r["selected_patterns"] for r in per_year]),
            "calibration": calibration(oos_df) if len(oos_df) else [],
            "brier": float(np.mean((oos_df["p"] - oos_df["win"]) ** 2)) if len(oos_df) else None,
            "brier_base": float(np.mean((oos_df["win"].mean() - oos_df["win"]) ** 2)) if len(oos_df) else None,
            "oos": oos_df, "kept": kept}


def _combine(rows: list[dict]) -> dict:
    n = sum(r["trades"] for r in rows)
    if not n:
        return {"trades": 0, "win_rate": None, "avg_ret": None}
    return {"trades": n, "win_rate": sum((r["win_rate"] or 0) * r["trades"] for r in rows) / n,
            "avg_ret": sum((r["avg_ret"] or 0) * r["trades"] for r in rows) / n}


def _pick_threshold(train: pd.DataFrame, min_keep: float = 0.2) -> float:
    """Probability cut-off that maximises the training average return while keeping >= 20% of trades."""
    best, best_thr = -1e9, 0.0
    for q in np.linspace(0, 1 - min_keep, 17):
        thr = float(train["p"].quantile(q))
        r = train.loc[train["p"] >= thr, "ret"]
        if len(r) and r.mean() > best:
            best, best_thr = r.mean(), thr
    return best_thr


def calibration(oos: pd.DataFrame, bins: int = 8) -> list[dict]:
    q = pd.qcut(oos["p"], bins, duplicates="drop")
    g = oos.groupby(q, observed=True).agg(predicted=("p", "mean"), actual=("win", "mean"), n=("win", "size"),
                                          avg_ret=("ret", "mean"))
    return [{"predicted": float(r.predicted), "actual": float(r.actual), "n": int(r.n), "avg_ret": float(r.avg_ret)}
            for r in g.itertuples()]


# --------------------------------------------------------------------------- overfitting and luck
def pbo(data: pd.DataFrame, blocks: int = 12, min_trades: int = 20) -> dict | None:
    """Probability of backtest overfitting (CSCV) for choosing the best pattern by average return."""
    months = data["signal"].dt.to_period("M")
    edges = np.array_split(np.sort(months.unique()), blocks)
    block_of = {m: k for k, ms in enumerate(edges) for m in ms}
    d = data.assign(block=months.map(block_of), key=data["pattern"] + "|" + data["direction"])
    sums = d.pivot_table(index="block", columns="key", values="ret", aggfunc="sum", fill_value=0.0)
    cnts = d.pivot_table(index="block", columns="key", values="ret", aggfunc="size", fill_value=0)
    cnts = cnts.reindex(index=sums.index, columns=sums.columns, fill_value=0)
    if sums.shape[1] < 4 or sums.shape[0] < blocks:
        return None
    logits, combos = [], list(itertools.combinations(range(blocks), blocks // 2))
    for is_blocks in combos:
        oos_blocks = [b for b in range(blocks) if b not in is_blocks]
        n_is, n_oos = cnts.iloc[list(is_blocks)].sum(), cnts.iloc[oos_blocks].sum()
        ok = (n_is >= min_trades) & (n_oos >= min_trades)
        if ok.sum() < 4:
            continue
        perf_is = (sums.iloc[list(is_blocks)].sum() / n_is.replace(0, np.nan))[ok]
        perf_oos = (sums.iloc[oos_blocks].sum() / n_oos.replace(0, np.nan))[ok]
        best = perf_is.idxmax()
        rank = (perf_oos.rank(pct=True)[best] * len(perf_oos)) / (len(perf_oos) + 1)
        logits.append(math.log(rank / (1 - rank)))
    if not logits:
        return None
    logits = np.array(logits)
    return {"pbo": float((logits <= 0).mean()), "combinations": int(len(logits)), "candidates": int(sums.shape[1])}


def fdr(data: pd.DataFrame, baseline: dict[str, float], q: float = 0.10) -> dict:
    """Benjamini-Hochberg: patterns whose win rate beats the random-day rate beyond luck."""
    rows = []
    for (pat, dirn), g in data.groupby(["pattern", "direction"]):
        n, w = len(g), int(g["win"].sum())
        p0 = baseline.get(dirn)
        if n < MIN_TRADES or p0 is None or not 0 < p0 < 1:
            continue
        z = (w / n - p0) / math.sqrt(p0 * (1 - p0) / n)
        pval = 0.5 * math.erfc(z / math.sqrt(2))          # one-sided: better than random
        rows.append((pat, dirn, n, w / n, p0, pval))
    rows.sort(key=lambda r: r[5])
    m = len(rows)
    passed = 0
    for k, r in enumerate(rows, 1):
        if r[5] <= q * k / m:
            passed = k
    return {"tested": m, "survive": passed, "q": q,
            "patterns": [{"pattern": r[0], "direction": r[1], "trades": r[2], "win_rate": r[3], "baseline": r[4],
                          "p_value": r[5], "survives": i < passed} for i, r in enumerate(rows[:12])]}


def bootstrap(trades: pd.DataFrame, n: int = 2000, seed: int = 7) -> dict | None:
    """Month-block bootstrap of the average trade return (keeps the clustering of trades in time)."""
    if trades.empty:
        return None
    months = trades["signal"].dt.to_period("M")
    groups = [g["ret"].to_numpy() for _, g in trades.groupby(months)]
    rng = np.random.default_rng(seed)
    means = np.empty(n)
    for k in range(n):
        pick = rng.integers(0, len(groups), len(groups))
        r = np.concatenate([groups[i] for i in pick])
        means[k] = r.mean()
    return {"low": float(np.quantile(means, 0.05)), "median": float(np.median(means)),
            "high": float(np.quantile(means, 0.95)), "p_positive": float((means > 0).mean())}


# --------------------------------------------------------------------------- the whole study
def run(prices: dict[str, pd.DataFrame], index_df: pd.DataFrame | None, progress=None, l2: float = 1.0,
        instrument: str = "option", cost: float = 0.0) -> dict:
    """instrument "option" (the picks as option trades) or "spot" (the same signals traded as shares)."""
    progress = progress or (lambda m, f: None)
    st = load_strategy()
    data = build_dataset(prices, index_df, st, progress, instrument, cost)
    if data.empty:
        raise ValueError("No signal trades to study")
    progress("Walk-forward test, year by year", 0.6)
    wf = walk_forward(data, l2=l2)
    progress("Overfitting and luck checks", 0.85)
    regime = market_regime(index_df)
    base_rows = []
    for t, df in list(prices.items())[:60]:
        b = trade_outcomes(df, t, strategy=st, regime=regime, baseline_step=5, instrument=instrument, cost=cost)
        base_rows.append(b[b["pattern"].str.startswith("_baseline") & ~b["skipped"]])
    base = pd.concat(base_rows) if base_rows else pd.DataFrame()
    baseline = {d: float((g["ret"] > 0).mean()) for d, g in base.groupby("direction")} if len(base) else {}
    first_test = pd.Timestamp(min(r["year"] for r in wf["per_year"]), 1, 1) if wf["per_year"] else data["signal"].max()
    train_part = data[data["exit"] < first_test]
    out = {
        "as_of": str(max(df.index[-1] for df in prices.values()).date()), "universe": len(prices),
        "instrument": instrument, "cost": cost,
        "strategy": st.label(), "signals": int(len(data)), "from": str(data["signal"].min().date()),
        "walk_forward": {k: v for k, v in wf.items() if k not in ("oos", "kept")},
        "bootstrap_all": bootstrap(wf["oos"]), "bootstrap_meta": bootstrap(wf["kept"]),
        "pbo": pbo(data), "fdr": fdr(train_part if len(train_part) > 2000 else data, baseline),
        "baseline_win": baseline,
    }
    out["verdict"] = verdict(out)
    # the meta model fitted on everything, for scoring today's signals
    X, mu, sd = _design(data)
    w = fit_logistic(X, data["win"].to_numpy(float), l2)
    thr = _pick_threshold(data.assign(p=predict(w, X)))       # same rule each walk-forward year used
    out["model"] = {"weights": w.tolist(), "mu": mu.tolist(), "sd": sd.tolist(), "threshold": thr,
                    "features": FEATURES, "patterns": PATTERN_NAMES, "validated": out["verdict"]["meta_helps"]}
    progress("Done", 1.0)
    return out


def verdict(r: dict) -> dict:
    wf = r["walk_forward"]
    a, m = wf["all"], wf["meta"]
    meta_helps = bool(m["trades"] and a["trades"] and m["avg_ret"] is not None
                      and m["avg_ret"] > a["avg_ret"] and m["win_rate"] > a["win_rate"]
                      and sum(1 for y in wf["per_year"] if y["meta"]["avg_ret"] is not None
                              and y["all"]["avg_ret"] is not None and y["meta"]["avg_ret"] > y["all"]["avg_ret"])
                      >= math.ceil(len(wf["per_year"]) * 0.6))
    profitable = bool(m["avg_ret"] is not None and m["avg_ret"] > 0 and (r.get("bootstrap_meta") or {}).get("low", -1) > 0)
    lines = []
    what = "share trade" if r.get("instrument") == "spot" else "option trade"
    lines.append(f"Walk-forward, all signals: {a['win_rate']:.0%} won, average {a['avg_ret']:+.1%} per {what} "
                 f"({a['trades']:,} trades, each year judged by rules chosen only from earlier years).")
    if m["trades"]:
        lines.append(f"With the meta-label filter: {m['win_rate']:.0%} won, average {m['avg_ret']:+.1%} "
                     f"({m['trades']:,} trades kept). The filter {'improved' if meta_helps else 'did not reliably improve'} "
                     f"results in most years.")
    if r.get("pbo"):
        lines.append(f"Probability of backtest overfitting: {r['pbo']['pbo']:.0%} (above 50% means picking the "
                     f"best-looking pattern is mostly picking luck).")
    if r.get("fdr"):
        lines.append(f"{r['fdr']['survive']} of {r['fdr']['tested']} patterns beat a random day after correcting for luck.")
    b = r.get("bootstrap_meta") or r.get("bootstrap_all")
    if b:
        lines.append(f"Bootstrap 90% range of the average trade: {b['low']:+.1%} to {b['high']:+.1%}; "
                     f"chance it is above zero: {b['p_positive']:.0%}.")
    lines.append("Profitable after costs on unseen years: " + ("yes" if profitable else "no") + ".")
    return {"meta_helps": meta_helps, "profitable": profitable, "lines": lines}


def score_today(model: dict, df: pd.DataFrame, index_df: pd.DataFrame | None, pattern: str, direction: str) -> float | None:
    """The meta-label model's chance that a signal on the last bar of `df` wins."""
    try:
        f = stock_features(df, index_df).iloc[[-1]].copy()
        sign = 1.0 if direction == "bullish" else -1.0
        for col in ("trend50", "trend200", "mom20", "mom60", "rsi14", "from_high", "market_mom20"):
            f[col] = f[col] * sign
        if sign < 0:
            f["market_up"] = 1 - f["market_up"]
        d = f.assign(pattern=pattern, direction=direction)
        if d[FEATURES].isna().any(axis=None):
            return None
        X, _, _ = _design(d, np.array(model["mu"]), np.array(model["sd"]))
        return float(predict(np.array(model["weights"]), X)[0])
    except Exception:
        return None
