"""Built-in Yahoo client (used on phones without yfinance) and the Termux helpers. No network."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from stock_agent import app, data, yahoo

DAY = 86400


def payload(ts, closes, adj=None, tz="Asia/Kolkata", error=None):
    if error:
        return {"chart": {"result": None, "error": {"code": "Not Found", "description": error}}}
    q = {"open": closes, "high": [None if c is None else c + 1 for c in closes],
         "low": [None if c is None else c - 1 for c in closes], "close": closes,
         "volume": [100] * len(closes)}
    ind = {"quote": [q]}
    if adj:
        ind["adjclose"] = [{"adjclose": adj}]
    return {"chart": {"result": [{"meta": {"exchangeTimezoneName": tz, "longName": "Reliance Industries Limited"},
                                  "timestamp": ts, "indicators": ind}], "error": None}}


class Resp:
    def __init__(self, body, code=200):
        self.body, self.status_code = body, code

    def json(self):
        return self.body


class YahooClientTests(unittest.TestCase):
    def setUp(self):
        yahoo._session = None
        self.sleep = mock.patch.object(yahoo.time, "sleep", lambda s: None)
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()

    def get(self, *responses):
        sess = mock.Mock()
        sess.get.side_effect = list(responses)
        return mock.patch.object(yahoo.requests, "Session", return_value=sess), sess

    def test_daily_bars_are_adjusted_and_indexed_by_date(self):
        t0 = 1790000000 - 1790000000 % DAY + 3 * 3600 + 45 * 60   # 09:15 IST
        body = payload([t0, t0 + DAY, t0 + 2 * DAY], [100.0, None, 110.0], adj=[50.0, None, 55.0])
        p, sess = self.get(Resp(body))
        with p:
            df, meta = yahoo.chart("RELIANCE.NS", "10y", "1d")
        self.assertEqual(len(df), 2)                                 # the empty bar is dropped
        self.assertIsNone(df.index.tz)
        self.assertEqual(df.index[0], pd.Timestamp(pd.Timestamp(t0, unit="s", tz="UTC").tz_convert("Asia/Kolkata").date()))
        self.assertEqual(list(df["Close"]), [50.0, 55.0])            # scaled by adjclose / close
        self.assertEqual(list(df["High"]), [50.5, 55.5])
        self.assertEqual(meta["longName"], "Reliance Industries Limited")
        self.assertEqual(sess.get.call_args.kwargs["headers"]["User-Agent"], "Mozilla/5.0")
        self.assertEqual(sess.get.call_args.kwargs["params"]["range"], "10y")

    def test_intraday_drops_the_live_tick_off_the_grid(self):
        t0 = 1790000000 - 1790000000 % 900
        p, _ = self.get(Resp(payload([t0, t0 + 900, t0 + 900 + 838], [1.0, 2.0, 3.0], tz="America/New_York")))
        with p:
            df, _ = yahoo.chart("GC=F", "60d", "15m")
        self.assertEqual(list(df["Close"]), [1.0, 2.0])
        self.assertEqual(str(df.index.tz), "America/New_York")

    def test_rate_limit_is_retried_on_the_other_host(self):
        t0 = 1790000000
        p, sess = self.get(Resp({}, 429), Resp(payload([t0], [5.0])))
        with p:
            df, _ = yahoo.chart("TCS.NS", "1mo", "1d")
        self.assertEqual(float(df["Close"].iloc[0]), 5.0)
        hosts = [c.args[0].split("/")[2] for c in sess.get.call_args_list]
        self.assertEqual(hosts, ["query1.finance.yahoo.com", "query2.finance.yahoo.com"])

    def test_errors_and_unknown_periods(self):
        p, _ = self.get(Resp(payload([], [], error="No data found, symbol may be delisted")))
        with p, self.assertRaises(yahoo.YahooError):
            yahoo.chart("NOPE.NS", "1y", "1d")
        with self.assertRaises(yahoo.YahooError):
            yahoo.chart("TCS.NS", "fortnight", "1d")

    def test_download_skips_failures(self):
        good = (pd.DataFrame({"Close": [1.0]}), {})
        with mock.patch.object(yahoo, "chart", side_effect=lambda t, *a, **k: good if t == "A" else (_ for _ in ()).throw(yahoo.YahooError("x"))):
            got = yahoo.download(["A", "B"], "1y", "1d")
        self.assertEqual(list(got), ["A"])

    def test_use_builtin_follows_env_and_import(self):
        with mock.patch.dict(os.environ, {"STOCK_AGENT_PRICES": "yahoo"}):
            self.assertTrue(yahoo.use_builtin())
        with mock.patch.dict(os.environ, {"STOCK_AGENT_PRICES": ""}), mock.patch.dict("sys.modules", {"yfinance": None}):
            self.assertTrue(yahoo.use_builtin())


class RefreshWithoutYfinanceTests(unittest.TestCase):
    def test_refresh_many_writes_cache_from_builtin_client(self):
        idx = pd.DatetimeIndex(pd.bdate_range("2026-01-01", periods=300))
        frame = pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 10.0}, index=idx)
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(yahoo, "use_builtin", return_value=True), \
                mock.patch.object(yahoo, "download", return_value={"AAA.NS": frame}) as dl:
            failed = data.refresh_many(["AAA.NS", "BBB.NS"], "10y", "1d", tmp)
        self.assertEqual(failed, ["BBB.NS"])
        self.assertEqual(dl.call_args.args[1:], ("10y", "1d"))
        cached = data.read_cached(data._cache_path(tmp, "AAA.NS", "10y", "1d"))
        self.assertEqual(len(cached), 300)


class TermuxTests(unittest.TestCase):
    def test_open_url_uses_android_when_on_termux(self):
        with mock.patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr"}), \
                mock.patch("shutil.which", return_value="/usr/bin/termux-open-url"), \
                mock.patch("subprocess.run") as run, mock.patch.object(app.webbrowser, "open") as wb:
            app.open_url("http://127.0.0.1:8765/")
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["termux-open-url", "http://127.0.0.1:8765/"])
        wb.assert_not_called()

    def test_open_url_uses_webbrowser_elsewhere(self):
        with mock.patch.dict(os.environ, {"PREFIX": "/usr", "TERMUX_VERSION": ""}), \
                mock.patch.object(app.webbrowser, "open") as wb:
            app.open_url("http://127.0.0.1:8765/")
        wb.assert_called_once()

    def test_installer_is_valid_bash_and_skips_yfinance(self):
        import shutil
        import subprocess

        script = Path(__file__).resolve().parent.parent / "termux" / "install.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn("--no-deps", text)
        self.assertIn("python-pandas", text)
        self.assertNotIn("yfinance ", text.split("--no-deps")[0].split("pipi requests")[-1])
        if shutil.which("bash"):
            self.assertEqual(subprocess.run(["bash", "-n", str(script)]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
