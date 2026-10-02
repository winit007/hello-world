"""Price data loading with a local cache so the agent can run fully offline."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

DEFAULT_CACHE = Path(os.environ.get("STOCK_AGENT_CACHE", ".cache"))
COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class OfflineDataMissing(RuntimeError):
    """Raised when offline mode is requested but no cached data exists."""


def _cache_path(cache_dir: Path, ticker: str, period: str, interval: str) -> Path:
    return cache_dir / "prices" / f"{ticker.upper()}_{period}_{interval}.csv"


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance may return a (field, ticker) MultiIndex; reduce it to plain columns."""
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [c[0] for c in df.columns]
    df = df[[c for c in COLUMNS if c in df.columns]].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "Date"
    return df.dropna(subset=["Open", "High", "Low", "Close"]).astype(float)


def download_prices(ticker: str, period: str = "5y", interval: str = "1d") -> pd.DataFrame:
    import yfinance as yf  # imported lazily so offline runs never need it

    raw = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
    if raw is None or raw.empty:
        raise RuntimeError(f"No price data returned for {ticker!r}")
    return _flatten(raw)


def load_prices(
    ticker: str,
    period: str = "5y",
    interval: str = "1d",
    offline: bool = False,
    cache_dir: Path = DEFAULT_CACHE,
) -> pd.DataFrame:
    """Return an OHLCV frame. Online: download and refresh the cache. Offline: cache only."""
    path = _cache_path(cache_dir, ticker, period, interval)
    if offline:
        if not path.exists():
            raise OfflineDataMissing(
                f"No cached prices for {ticker} ({period}/{interval}). "
                "Run once without --offline to populate the cache."
            )
        return read_cached(path)
    try:
        df = download_prices(ticker, period, interval)
    except Exception as exc:  # network down, rate limited, etc.
        if path.exists():
            print(f"[data] download failed ({exc}); using cached prices for {ticker}")
            return read_cached(path)
        raise
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    return df


def read_cached(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col="Date", parse_dates=True)
    return df[COLUMNS].astype(float)


FRESH_SECONDS = 2 * 3600  # a cached file younger than this is used as is


def _is_fresh(path: Path) -> bool:
    import time as _t

    return path.exists() and (_t.time() - path.stat().st_mtime) < FRESH_SECONDS


def _split(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Break a multi-ticker yfinance frame into one clean OHLCV frame per ticker."""
    out = {}
    if raw is None or raw.empty:
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = set(raw.columns.get_level_values(0))
        for t in tickers:
            if t in level0:
                part = raw[t].dropna(how="all")
                if not part.empty:
                    out[t] = _flatten(part)
    elif len(tickers) == 1:
        out[tickers[0]] = _flatten(raw)
    return out


def refresh_many(tickers: list[str], period: str = "10y", interval: str = "1d", cache_dir: Path = DEFAULT_CACHE,
                 chunk: int = 80, progress=None) -> list[str]:
    """Bring the price cache of many tickers up to date with as few requests as possible.

    New tickers get their full history; cached ones only the last 3 months, appended to the cache. If the
    overlap disagrees by more than 1% (a split or bonus re-adjusted the history) the full history is
    downloaded again. Returns the tickers that could not be refreshed.
    """
    import yfinance as yf

    paths = {t: _cache_path(cache_dir, t, period, interval) for t in tickers}
    stale = [t for t in tickers if not _is_fresh(paths[t])]
    update = [t for t in stale if paths[t].exists()]
    full = [t for t in stale if not paths[t].exists()]
    failed: list[str] = []
    done = 0

    def batches(lst):
        for k in range(0, len(lst), chunk):
            yield lst[k:k + chunk]

    def fetch(group, per):
        try:
            raw = yf.download(group, period=per, interval=interval, progress=False, auto_adjust=True,
                              group_by="ticker", threads=True)
        except Exception:
            raw = None
        return _split(raw, group)

    total = max(len(stale), 1)
    for group in batches(update):
        got = fetch(group, "3mo")
        for t in group:
            new = got.get(t)
            if new is None or new.empty:
                failed.append(t)
                continue
            old = read_cached(paths[t])
            common = old.index.intersection(new.index)
            if len(common) and (abs(old.loc[common, "Close"] / new.loc[common, "Close"] - 1).max() > 0.01):
                full.append(t)  # history was re-adjusted: fetch it all again
                continue
            merged = pd.concat([old[old.index < new.index[0]], new])
            merged.to_csv(paths[t])
        done += len(group)
        if progress:
            progress(f"Updating prices {done}/{len(stale)}", 0.6 * done / total)
    for group in batches(full):
        got = fetch(group, period)
        for t in group:
            df = got.get(t)
            if df is None or df.empty:
                failed.append(t)
                continue
            paths[t].parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(paths[t])
        done += len(group)
        if progress:
            progress(f"Downloading prices {min(done, len(stale))}/{len(stale)}", 0.6 * min(done, total) / total)
    return failed
