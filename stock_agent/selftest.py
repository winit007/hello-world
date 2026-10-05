"""A quick check that everything the app needs works on this device (used by the Android app's build tests).

GET /api/selftest runs each check on its own and reports what worked, so a missing library or a blocked network
shows up as one failed line instead of a broken page.
"""
from __future__ import annotations

import platform
import sys
import time


def _check(name: str, fn) -> dict:
    t = time.time()
    try:
        detail = fn()
        return {"name": name, "ok": True, "detail": str(detail)[:200], "seconds": round(time.time() - t, 2)}
    except Exception as exc:                      # report, never raise
        return {"name": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"[:300], "seconds": round(time.time() - t, 2)}


def run(network: bool = True) -> dict:
    def versions():
        import numpy
        import pandas

        return f"python {platform.python_version()}, pandas {pandas.__version__}, numpy {numpy.__version__}"

    def frames():
        import numpy as np
        import pandas as pd

        from .data import MONTH_END

        s = pd.Series(np.arange(1, 400, dtype=float), index=pd.bdate_range("2024-01-01", periods=399))
        month = s.resample(MONTH_END).last().pct_change().dropna()
        return f"{len(month)} months, last return {month.iloc[-1]:.4f}, rolling {s.rolling(20).mean().iloc[-1]:.2f}"

    def timezones():
        from zoneinfo import ZoneInfo

        import datetime as dt

        return dt.datetime(2026, 10, 5, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(ZoneInfo("America/New_York")).isoformat()

    def options():
        from .options import bs_price

        return f"call {bs_price(100, 100, 0.1, 0.25, 'call', 0.065):.3f}"

    def libraries():
        import feedparser  # noqa: F401
        import qrcode  # noqa: F401
        import vaderSentiment.vaderSentiment as v

        return f"vader {v.SentimentIntensityAnalyzer().polarity_scores('great results')['compound']:.2f}"

    def optimisation():
        from . import robust

        import numpy as np

        X = np.c_[np.ones(200), np.random.default_rng(1).normal(size=(200, 3))]
        y = (X[:, 1] + 0.1 * np.random.default_rng(2).normal(size=200) > 0).astype(float)
        w = robust.fit_logistic(X, y)
        return f"logistic weights {np.round(w[:2], 2).tolist()}"

    def files():
        from . import app

        return f"index.html {len((app.WEB / 'index.html').read_text(encoding='utf-8'))} bytes, home {app.HOME}"

    def yahoo():
        from . import yahoo as Y

        df, meta = Y.chart("NIFTYBEES.NS", "1mo", "1d")
        return f"{len(df)} days, last close {float(df['Close'].iloc[-1]):.2f}"

    def nse_file():
        import requests

        r = requests.get("https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv", timeout=20,
                         headers={"User-Agent": "Mozilla/5.0"})
        return f"HTTP {r.status_code}, {len(r.text)} bytes"

    checks = [("versions", versions), ("pandas and numpy maths", frames), ("time zones", timezones), ("option pricing", options),
              ("feedparser, qrcode, vader", libraries), ("logistic fit", optimisation), ("app files", files)]
    if network:
        checks += [("Yahoo prices over HTTPS", yahoo), ("NSE list over HTTPS", nse_file)]
    results = [_check(n, f) for n, f in checks]
    return {"ok": all(r["ok"] for r in results), "python": sys.version.split()[0], "platform": platform.platform(), "checks": results}
