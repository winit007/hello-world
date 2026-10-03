"""Stock lists for the screener.

Indian lists come from NSE's own files (index constituents, the full equity list, the F&O lot file) and
are refreshed at most once a day; dated copies ship with the package for offline use. Yahoo Finance
symbols are the NSE symbol plus ".NS".

  nifty50   Nifty 50 constituents
  nifty100  Nifty 100 constituents
  fno       every stock with NSE options (about 215): the ones this app can place option orders on
  nse_all   every NSE stock in the main EQ series (about 2,300); illiquid ones are dropped by the
            screener's turnover filter
  sp500     S&P 500 constituents (US)
  us        30 US mega caps
"""
from __future__ import annotations

import io
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

LISTS = Path(__file__).with_name("lists")
NSE = "https://nsearchives.nseindia.com/content"
SOURCES = {
    "nifty50": (f"{NSE}/indices/ind_nifty50list.csv", "nifty50.csv"),
    "nifty100": (f"{NSE}/indices/ind_nifty100list.csv", "nifty100.csv"),
    "nifty200": (f"{NSE}/indices/ind_nifty200list.csv", "nifty200.csv"),
    "nifty500": (f"{NSE}/indices/ind_nifty500list.csv", "nifty500.csv"),
    "niftytotal": (f"{NSE}/indices/ind_niftytotalmarket_list.csv", "niftytotal.csv"),
    "microcap250": (f"{NSE}/indices/ind_niftymicrocap250_list.csv", "microcap250.csv"),
    "nse_equity": (f"{NSE}/equities/EQUITY_L.csv", "nse_equity.csv"),
    "sp500": ("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv", "sp500.csv"),
}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

US_MEGACAP = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "BRK-B", "JPM", "V", "MA", "UNH",
    "XOM", "LLY", "JNJ", "PG", "HD", "COST", "ABBV", "MRK", "KO", "PEP", "BAC", "WMT", "NFLX", "AMD",
    "CRM", "ORCL", "ADBE",
]

NAMES = {
    "nifty50": "Nifty 50", "nifty100": "Nifty 100", "nifty200": "Nifty 200", "nifty500": "Nifty 500", "fno": "All F&O stocks (NSE)",
    "nse_all": "All NSE stocks", "sp500": "S&P 500 (US)", "us": "US mega caps",
}
CHOICES = list(NAMES)


def _cache_dir() -> Path:
    return Path.home() / ".stock_agent" / "lists"


def _fetch(key: str, offline: bool = False) -> str:
    """Text of one list: today's cached copy, a fresh download, an older copy, or the bundled one."""
    url, fname = SOURCES[key]
    cached = _cache_dir() / fname
    if cached.exists() and date.fromtimestamp(cached.stat().st_mtime) == date.today():
        return cached.read_text(encoding="utf-8")
    if not offline:
        import requests

        for attempt in range(3):  # NSE's archive host rate-limits now and then
            try:
                r = requests.get(url, headers=UA, timeout=20)
                if r.ok and not r.text.lstrip().startswith("<"):
                    cached.parent.mkdir(parents=True, exist_ok=True)
                    cached.write_text(r.text, encoding="utf-8")
                    return r.text
            except Exception:
                pass
            time.sleep(1.5 * (attempt + 1))
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    return (LISTS / fname).read_text(encoding="utf-8")


def _frame(key: str, offline: bool = False) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(_fetch(key, offline)), dtype=str)
    df.columns = [c.strip() for c in df.columns]
    return df.apply(lambda col: col.str.strip() if col.dtype == object else col)


def nse_equity(offline: bool = False) -> pd.DataFrame:
    """Every NSE-listed equity with its listing date."""
    df = _frame("nse_equity", offline)
    df["listed"] = pd.to_datetime(df["DATE OF LISTING"], format="%d-%b-%Y", errors="coerce")
    return df


def get_universe(name: str, offline: bool = False) -> list[str]:
    if name in ("nifty50", "nifty100", "nifty200", "nifty500"):
        return [s + ".NS" for s in _frame(name, offline)["Symbol"]]
    if name == "fno":
        from .lots import load_nse_lots

        lots, _ = load_nse_lots(offline)
        index_names = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "NIFTYFPI"}
        return sorted(s + ".NS" for s in lots if s not in index_names)
    if name == "nse_all":
        df = nse_equity(offline)
        return sorted(s + ".NS" for s in df.loc[df["SERIES"] == "EQ", "SYMBOL"])
    if name == "sp500":
        return [s.replace(".", "-") for s in _frame("sp500", offline)["Symbol"]]  # BRK.B -> BRK-B on Yahoo
    if name == "us":
        return list(US_MEGACAP)
    raise KeyError(f"unknown universe {name!r}; choose from {', '.join(CHOICES)}")


def industries(offline: bool = False) -> dict[str, str]:
    """Yahoo symbol -> NSE industry, from the index lists (Total Market and Microcap 250 cover ~1,000 stocks)."""
    out = {}
    for key in ("microcap250", "niftytotal", "nifty500", "nifty50", "nifty100", "nifty200"):
        try:
            df = _frame(key, offline)
            out.update({s + ".NS": ind for s, ind in zip(df["Symbol"], df["Industry"])})
        except Exception:
            continue
    return out


def index_symbol(name: str) -> str:
    """Yahoo symbol of the benchmark index for a universe."""
    return "^GSPC" if name in ("sp500", "us") else "^NSEI"


def recent_listings(days: int = 365, offline: bool = False) -> pd.DataFrame:
    """Stocks first listed on NSE in the last `days` days (IPOs, plus demergers and SME migrations)."""
    df = nse_equity(offline)
    cutoff = pd.Timestamp(datetime.now().date() - timedelta(days=days))
    out = df[df["listed"] >= cutoff].sort_values("listed", ascending=False)
    return out.rename(columns={"SYMBOL": "symbol", "NAME OF COMPANY": "name", "SERIES": "series"})[
        ["symbol", "name", "series", "listed"]].reset_index(drop=True)


# Back-compat: static snapshot lists for code that just wants a quick default.
UNIVERSES = {"us": US_MEGACAP}
