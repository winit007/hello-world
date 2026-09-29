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
