"""A small Yahoo Finance price client, used when the yfinance package is not installed (e.g. on Android/Termux).

It reads Yahoo's public chart endpoint with plain `requests`. Yahoo refuses requests that claim to be a full
desktop browser without behaving like one, so the user agent is a bare "Mozilla/5.0". Frames match what
`yfinance.download(..., auto_adjust=True)` returns: Open/High/Low/Close/Volume, with prices adjusted for
splits and dividends when `adjust` is set.

Set STOCK_AGENT_PRICES=yahoo to use it even when yfinance is installed.
"""
from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
import pandas as pd
import requests

HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
AGENTS = ("Mozilla/5.0", "Mozilla/5.0 (compatible)")
RANGES = {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"}
_session = None


class YahooError(RuntimeError):
    pass


def use_builtin() -> bool:
    """True when prices should come from this module rather than yfinance."""
    if os.environ.get("STOCK_AGENT_PRICES", "").lower() == "yahoo":
        return True
    try:
        import yfinance  # noqa: F401
    except Exception:
        return True
    return False


def _get(symbol: str, params: dict, retries: int = 4) -> dict:
    global _session
    if _session is None:
        _session = requests.Session()
    last = None
    for attempt in range(retries):
        host, agent = HOSTS[attempt % 2], AGENTS[(attempt // 2) % 2]
        try:
            r = _session.get(f"https://{host}/v8/finance/chart/{requests.utils.quote(symbol)}", params=params,
                             headers={"User-Agent": agent, "Accept": "application/json"}, timeout=20)
            if r.status_code == 429:
                last = "rate limited (429)"
                time.sleep(1.5 * (attempt + 1))
                continue
            body = r.json().get("chart", {})
            if body.get("error"):
                raise YahooError(f"{symbol}: {body['error'].get('description') or body['error']}")
            res = (body.get("result") or [None])[0]
            if not res:
                raise YahooError(f"No price data for {symbol}")
            return res
        except YahooError:
            raise
        except Exception as exc:  # network blip, bad JSON
            last = str(exc)
            time.sleep(1.0 * (attempt + 1))
    raise YahooError(f"{symbol}: {last}")


def _exchange_tz(meta: dict):
    """The exchange's time zone; a fixed UTC offset when the zone database is missing (Android has none)."""
    name = meta.get("exchangeTimezoneName")
    if name:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(name)
            return name
        except Exception:
            pass
    return timezone(timedelta(seconds=int(meta.get("gmtoffset") or 0)))


def chart(symbol: str, period: str = "1y", interval: str = "1d", adjust: bool = True) -> tuple[pd.DataFrame, dict]:
    """(OHLCV frame, meta) for one symbol. Daily bars are indexed by plain dates; intraday bars by time in
    the exchange's time zone."""
    params = {"interval": interval, "includePrePost": "false", "events": "div,splits"}
    if period in RANGES or re.fullmatch(r"\d+d", period):   # Yahoo also takes any "<N>d" range
        params["range"] = period
    else:
        raise YahooError(f"Unsupported period {period!r}")
    res = _get(symbol, params)
    meta = res.get("meta", {})
    ts = res.get("timestamp") or []
    if not ts:
        raise YahooError(f"No price data for {symbol}")
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({"Open": q.get("open"), "High": q.get("high"), "Low": q.get("low"),
                       "Close": q.get("close"), "Volume": q.get("volume")},
                      index=pd.to_datetime(ts, unit="s", utc=True).tz_convert(_exchange_tz(meta)))
    if adjust and interval.endswith(("d", "wk", "mo")):
        adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose")
        if adj:
            ratio = pd.Series(adj, index=df.index, dtype=float) / df["Close"]
            for c in ("Open", "High", "Low", "Close"):
                df[c] = df[c] * ratio
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    m = re.fullmatch(r"(\d+)m", interval)
    if m:   # Yahoo appends a live tick off the bar grid (e.g. 16:59:58 on 15-minute bars); yfinance drops it
        step = int(m.group(1)) * 60
        secs = (df.index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)
        df = df[secs % step == 0]
    df["Volume"] = df["Volume"].fillna(0)
    if interval.endswith(("d", "wk", "mo")):
        df.index = pd.DatetimeIndex(df.index.date)          # one row per trading day, like yfinance
        df = df[~df.index.duplicated(keep="last")]
    df.index.name = "Date"
    return df.astype(float), meta


def download(tickers: list[str], period: str, interval: str, adjust: bool = True, workers: int = 4) -> dict[str, pd.DataFrame]:
    """Frames for many symbols, a few requests at a time. Symbols that fail are left out."""
    def one(t):
        try:
            return t, chart(t, period, interval, adjust)[0]
        except Exception:
            return t, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return {t: df for t, df in pool.map(one, tickers) if df is not None and not df.empty}


def long_name(symbol: str) -> str | None:
    try:
        meta = _get(symbol, {"range": "5d", "interval": "1d"}).get("meta", {})
        return meta.get("longName") or meta.get("shortName")
    except Exception:
        return None
