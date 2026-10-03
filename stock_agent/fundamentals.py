"""Company fundamentals for My screener, known on each date only once they were published.

Sources (free, no login):
  Yahoo fundamentals timeseries   yearly results: earnings per share, net profit, sales, operating profit (EBIT),
                                  shareholders' equity, debt, share count, dividends paid. About four years.
  NSE shareholding filings        promoter holding every quarter, dated by the day NSE published it, and the
                                  promoters' pledged shares from the latest filing (today's picks only).

No look-ahead: a year's results count from 60 days after the year ends (Indian companies must publish yearly
results within 60 days); a shareholding filing counts from its publication date. Market value on a past day is
that day's split-adjusted price times today's share count, so splits do not distort P/E (share buybacks and new
issues in between are ignored).
"""
from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

YAHOO = "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{sym}"
FIELDS = {"annualDilutedEPS": "eps", "annualNetIncome": "ni", "annualTotalRevenue": "revenue", "annualEBIT": "ebit",
          "annualStockholdersEquity": "equity", "annualTotalDebt": "debt", "annualOrdinarySharesNumber": "shares",
          "annualCashDividendsPaid": "dividends"}
NSE_MASTER = "https://www.nseindia.com/api/corporate-share-holdings-master?index=equities&symbol={sym}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/json,text/xml,*/*"}
PUBLISH_LAG = pd.Timedelta(days=60)
MAX_AGE = 7 * 86400              # refresh a company's figures once a week

METRICS = {
    "pe": ("P/E: price / earnings", "level"),
    "pb": ("P/B: price / book value", "level"),
    "roe": ("Return on equity (ROE)", "pct"),
    "roce": ("Return on capital employed (ROCE)", "pct"),
    "de": ("Debt to equity", "level"),
    "eps_growth": ("Earnings growth, last year", "pct"),
    "sales_growth": ("Sales growth, last year", "pct"),
    "net_margin": ("Net profit margin", "pct"),
    "div_yield": ("Dividend yield", "pct"),
    "mcap_cr": ("Market value (₹ crore)", "level"),
    "promoter": ("Promoter holding", "pct"),
    "promoter_chg": ("Promoter holding change, 1 year", "pct"),
}

_session = None


def _get(url: str, timeout: int = 20) -> requests.Response:
    global _session
    if _session is None:
        _session = requests.Session()
    last = None
    for attempt in range(3):
        try:
            r = _session.get(url, headers=UA, timeout=timeout)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            return r
        except Exception as exc:          # network blip
            last = exc
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def _yahoo(sym: str) -> dict:
    url = YAHOO.format(sym=requests.utils.quote(sym)) + f"?type={','.join(FIELDS)}&period1=1262304000&period2={int(time.time()) + 86400}"
    out: dict[str, dict[str, float]] = {}
    for res in _get(url).json().get("timeseries", {}).get("result", []) or []:
        key = (res.get("meta", {}).get("type") or [None])[0]
        for row in res.get(key) or []:
            if row and key in FIELDS:
                out.setdefault(row["asOfDate"], {})[FIELDS[key]] = row["reportedValue"]["raw"]
    return out


def _nse_master(sym: str) -> list[dict]:
    if not sym.endswith(".NS"):
        return []
    r = _get(NSE_MASTER.format(sym=requests.utils.quote(sym[:-3])))
    if not r.ok or not r.text.lstrip().startswith("["):
        return []
    rows = []
    for x in r.json():
        try:
            pub = datetime.strptime((x.get("broadcastDate") or x.get("submissionDate") or "")[:11], "%d-%b-%Y")
            rows.append({"published": pub.strftime("%Y-%m-%d"), "quarter": x.get("date"),
                         "promoter": float(x["pr_and_prgrp"]) / 100, "xbrl": x.get("xbrl")})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(rows, key=lambda r: r["published"])


def fetch(sym: str, cache_dir: Path, offline: bool = False) -> dict:
    """Yearly results and promoter holdings for one company, cached for a week."""
    path = Path(cache_dir) / "fundamentals" / f"{sym}.json"
    if path.exists() and (offline or time.time() - path.stat().st_mtime < MAX_AGE):
        try:
            return json.loads(path.read_text())
        except ValueError:
            pass
    if offline:
        return {}
    data = {}
    try:
        data["annual"] = _yahoo(sym)
    except Exception:
        data["annual"] = {}
    try:
        data["shareholding"] = _nse_master(sym)
    except Exception:
        data["shareholding"] = []
    if data["annual"] or data["shareholding"]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    elif path.exists():                   # keep last week's copy rather than nothing
        return json.loads(path.read_text())
    return data


def fetch_many(symbols: list[str], cache_dir: Path, offline: bool = False, progress=None, workers: int = 8) -> dict[str, dict]:
    out, done = {}, 0
    with ThreadPoolExecutor(workers) as pool:
        for sym, data in zip(symbols, pool.map(lambda s: fetch(s, cache_dir, offline), symbols)):
            out[sym] = data
            done += 1
            if progress and done % 20 == 0:
                progress(f"Company results {done} of {len(symbols)}", done / len(symbols))
    return out


def _annual_frame(data: dict) -> pd.DataFrame:
    rows = data.get("annual") or {}
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame.from_dict(rows, orient="index")
    df.index = pd.to_datetime(df.index)
    return df.sort_index().reindex(columns=list(FIELDS.values()))


def frames(closes: pd.DataFrame, data: dict[str, dict]) -> dict[str, pd.DataFrame]:
    """Daily fundamentals for every stock: each value from the day it became public, carried forward."""
    idx = closes.index
    out = {k: pd.DataFrame(np.nan, index=idx, columns=closes.columns) for k in METRICS}
    for sym in closes.columns:
        d = data.get(sym) or {}
        a = _annual_frame(d)
        px = closes[sym]
        if not a.empty:
            a = a.copy()
            prev_eq = a["equity"].shift(1)
            a["roe"] = a["ni"] / ((a["equity"] + prev_eq.fillna(a["equity"])) / 2)
            a["roce"] = a["ebit"] / (a["equity"] + a["debt"].fillna(0))
            a["de"] = a["debt"].fillna(0) / a["equity"]
            a["eps_growth"] = np.where(a["eps"].shift(1) > 0, a["eps"] / a["eps"].shift(1) - 1, np.nan)
            a["sales_growth"] = a["revenue"] / a["revenue"].shift(1) - 1
            a["net_margin"] = a["ni"] / a["revenue"]
            a.loc[a["equity"] <= 0, ["roe", "roce", "de"]] = np.nan
            a.index = a.index + PUBLISH_LAG
            daily = a.reindex(idx.union(a.index)).ffill().reindex(idx)
            daily = daily.where(pd.Series(idx >= a.index[0], index=idx), axis=0)
            shares = a["shares"].dropna()
            if len(shares):
                mcap = px * float(shares.iloc[-1])
                out["mcap_cr"][sym] = mcap / 1e7
                out["pe"][sym] = (mcap / daily["ni"]).where(daily["ni"] > 0)
                out["pb"][sym] = (mcap / daily["equity"]).where(daily["equity"] > 0)
                out["div_yield"][sym] = daily["dividends"].abs() / mcap
            for k in ("roe", "roce", "de", "eps_growth", "sales_growth", "net_margin"):
                out[k][sym] = daily[k]
        sh = d.get("shareholding") or []
        if sh:
            s = pd.Series({pd.Timestamp(r["published"]): r["promoter"] for r in sh}).sort_index()
            s = s[~s.index.duplicated(keep="last")]
            daily = s.reindex(idx.union(s.index)).ffill().reindex(idx)
            daily[idx < s.index[0]] = np.nan
            out["promoter"][sym] = daily
            year_ago = s.reindex(idx - pd.Timedelta(days=365), method="ffill").to_numpy()
            out["promoter_chg"][sym] = daily.to_numpy() - year_ago
    return out


# ---------------------------------------------------------------------------- promoter pledge (latest filing)
_TAG = re.compile(r'<[\w-]+:(NumberOfSharesEncumberedUnderPledged|NumberOfShares) contextRef="([\w]+)"[^>]*>([0-9.]+)<')


def pledge_from_xbrl(text: str) -> float | None:
    """Share of the promoters' shares that are pledged, from an NSE shareholding XBRL filing."""
    vals: dict[tuple[str, str], float] = {}
    for tag, ctx, num in _TAG.findall(text):
        vals[(tag, ctx)] = float(num)
    total = vals.get(("NumberOfShares", "ShareholdingOfPromoterAndPromoterGroup_ContextI"))
    if not total:
        return None
    pledged = vals.get(("NumberOfSharesEncumberedUnderPledged", "ShareholdingOfPromoterAndPromoterGroup_ContextI"))
    if pledged is None:
        pledged = sum(vals.get(("NumberOfSharesEncumberedUnderPledged", c), 0.0) for c in ("Indian_ContextI", "Foreign_ContextI"))
    return min(pledged / total, 1.0)


def pledge(sym: str, data: dict, cache_dir: Path, offline: bool = False) -> float | None:
    sh = (data or {}).get("shareholding") or []
    url = sh[-1].get("xbrl") if sh else None
    if not url:
        return None
    path = Path(cache_dir) / "fundamentals" / f"{sym}.pledge.json"
    if path.exists():
        try:
            c = json.loads(path.read_text())
            if c.get("url") == url:
                return c.get("pledge")
        except ValueError:
            pass
    if offline:
        return None
    try:
        r = _get(url, timeout=30)
        value = pledge_from_xbrl(r.text) if r.ok else None
    except Exception:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "pledge": value}))
    return value


def coverage_start(frame: pd.DataFrame, dates, share: float = 0.6):
    """First of `dates` on which at least `share` of the stocks have this measure."""
    for d in dates:
        row = frame.loc[:d].iloc[-1] if len(frame.loc[:d]) else None
        if row is not None and row.notna().mean() >= share:
            return d
    return None


def finite(v) -> float | None:
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) or pd.isna(v) else float(v)
