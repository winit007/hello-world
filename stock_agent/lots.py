"""Exchange lot sizes. NSE publishes them in fo_mktlots.csv; a dated snapshot ships with the package."""
from __future__ import annotations

import io
import time
from datetime import date
from pathlib import Path

import pandas as pd

from .data import DEFAULT_CACHE

NSE_LOTS_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
SNAPSHOT = Path(__file__).with_name("nse_lots_snapshot.csv")
US_CONTRACT = 100
INDIA_SUFFIXES = (".NS", ".BO")


def _parse(text: str) -> dict[str, dict[str, int]]:
    df = pd.read_csv(io.StringIO(text), dtype=str)
    df.columns = [c.strip() for c in df.columns]
    out: dict[str, dict[str, int]] = {}
    for _, row in df.iterrows():
        sym = str(row.get("SYMBOL", "")).strip().upper()
        if not sym or sym == "SYMBOL":
            continue
        months = {}
        for col in df.columns[2:]:
            v = str(row[col]).strip()
            if v.isdigit():
                months[col.upper()] = int(v)
        if months:
            out[sym] = months
    return out


def _download(retries: int = 3) -> str:
    import requests

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
    last = None
    for i in range(retries):
        try:
            r = requests.get(NSE_LOTS_URL, headers=headers, timeout=20)
            if r.ok and r.text.lstrip().upper().startswith("UNDERLYING"):
                return r.text
            last = f"HTTP {r.status_code}"
        except Exception as exc:  # pragma: no cover - network dependent
            last = str(exc)
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"NSE lot file unavailable ({last})")


def load_nse_lots(offline: bool = False, cache_dir: Path = DEFAULT_CACHE) -> tuple[dict, str]:
    """Return ({symbol: {"OCT-26": 700, ...}}, source description). Refreshed at most once a day."""
    cached = cache_dir / "nse_lots.csv"
    if cached.exists() and date.fromtimestamp(cached.stat().st_mtime) == date.today():
        return _parse(cached.read_text()), "NSE (cached today)"
    if not offline:
        try:
            text = _download()
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(text)
            return _parse(text), "NSE (live)"
        except Exception as exc:
            print(f"[lots] {exc}; using fallback")
    if cached.exists():
        return _parse(cached.read_text()), f"NSE (cached {date.fromtimestamp(cached.stat().st_mtime)})"
    return _parse(SNAPSHOT.read_text()), "bundled snapshot (Sep 2026), verify with your broker"


def lot_size(ticker: str, expiry: date | None = None, offline: bool = False, cache_dir: Path = DEFAULT_CACHE) -> tuple[int | None, str]:
    """Lot size for the contract month of `expiry`. US listings use 100. None if the stock has no F&O."""
    if not ticker.upper().endswith(INDIA_SUFFIXES):
        return US_CONTRACT, "standard US contract"
    lots, source = load_nse_lots(offline, cache_dir)
    months = lots.get(ticker.upper().split(".")[0])
    if not months:
        return None, f"{ticker} is not in NSE F&O ({source})"
    if expiry:
        key = expiry.strftime("%b-%y").upper()
        if key in months:
            return months[key], source
    return next(iter(months.values())), source
