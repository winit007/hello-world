"""Penny stocks and penny coins: parsing, every rule, the score, the automatic run. Synthetic data, no network."""
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from stock_agent import fundamentals as F
from stock_agent import pennycrypto as PC
from stock_agent import pennystocks as PS

IST = timezone(timedelta(hours=5, minutes=30))
END = pd.Timestamp("2026-10-01")
IDX = pd.bdate_range(end=END, periods=900)

BHAV = """SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
AAA, EQ, 01-Oct-2026, 20, 20, 21, 19, 20, 20.50, 20, 500000, 102.5, 900, 300000, 60.00
BBB, BE, 01-Oct-2026, 20, 20, 21, 19, 20, 20.00, 20, 500000, 100.0, 900, 300000, 60.00
CCC, EQ, 01-Oct-2026, 900, 900, 910, 890, 900, 905.00, 900, 1000, 90.0, 90, 600, 60.00
"""


def series(level, drift, n=len(IDX), pump=False):
    t = np.arange(n)
    s = level * np.exp(drift * t)
    if pump:                                    # flat, then four times in the last 40 days
        s = level * np.exp(0.0002 * t) * np.where(t > n - 41, 1 + 3 * (t - (n - 41)) / 40, 1.0)
    return pd.Series(s * (1 + 0.003 * np.sin(t / 3)), index=IDX)


def frame(close, volume):
    return pd.DataFrame({"Close": close, "Volume": float(volume)}, index=close.index)


def fundamentals(ni=2e8, equity=1e9, debt=1e8, shares=2e8, revenue=2e9, promoter=0.55):
    yr = lambda k: {"eps": ni / shares, "ni": ni * k, "revenue": revenue * k, "ebit": ni * 1.5 * k, "equity": equity, "debt": debt,
                    "shares": shares, "dividends": -1e7}
    return {"annual": {"2024-03-31": yr(0.8), "2025-03-31": yr(0.9), "2026-03-31": yr(1.0)},
            "shareholding": [{"published": "2025-07-15", "promoter": promoter - 0.01, "xbrl": None},
                             {"published": "2026-07-15", "promoter": promoter, "xbrl": "x"}]}


# name -> (price series, daily volume in shares, company results); each one is meant to pass or to fail exactly one rule
STOCKS = {
    "GOOD.NS": (series(20, 0.0007), 5e5, fundamentals(ni=3e8, equity=1e9)),
    "OKAY.NS": (series(25, 0.0005), 5e5, fundamentals(ni=1.2e8, equity=1e9, debt=3e8)),
    "LOSS.NS": (series(20, 0.0007), 5e5, fundamentals(ni=-1e8)),
    "DEBT.NS": (series(20, 0.0007), 5e5, fundamentals(debt=2e9)),
    "DOWN.NS": (series(30, -0.0006), 5e5, fundamentals()),
    "PUMP.NS": (series(10, 0.0, pump=True), 5e5, fundamentals()),
    "THIN.NS": (series(20, 0.0007), 5e3, fundamentals()),
    "TINY.NS": (series(20, 0.0007), 5e5, fundamentals(shares=2e7)),
    "PROMO.NS": (series(20, 0.0007), 5e5, fundamentals(promoter=0.05)),
}


class ParseTests(unittest.TestCase):
    def test_bhavcopy_keeps_equities_and_reads_the_date(self):
        df = PS.parse_bhavcopy(BHAV)
        self.assertEqual(list(df.index), ["AAA.NS", "CCC.NS"])
        self.assertEqual(df.loc["AAA.NS", "close"], 20.5)
        self.assertEqual(df.loc["AAA.NS", "deliv_per"], 60.0)
        self.assertEqual(df.attrs["date"], date(2026, 10, 1))
        with self.assertRaises(ValueError):
            PS.parse_bhavcopy("<html>blocked</html>")

    def test_rules_from_clamps(self):
        r = PS.rules_from({"top": 99, "bucket_pct": 90, "price_cap": 1, "min_lakh": 0, "uptrend": 0})
        self.assertEqual((r.top, r.bucket_pct, r.price_cap, r.min_lakh, r.uptrend), (25, 30.0, 5.0, 2.0, False))
        self.assertEqual(PS.rules_from({"nonsense": 1}).top, 10)

    def test_circuit_days(self):
        c = pd.DataFrame({"a": 100.0, "b": 100.0}, index=pd.bdate_range("2026-01-01", periods=70))
        c.iloc[-10:, 0] = [100 * 1.05 ** k for k in range(1, 11)]          # ten 5% days in a row
        self.assertEqual(int(PS.circuit_days(c)["a"]), 10)
        self.assertEqual(int(PS.circuit_days(c)["b"]), 0)


class CompanyTests(unittest.TestCase):
    def test_etfs_are_not_companies(self):
        names = {"ABC.NS": "ABC Industries Ltd.", "PHARMABEES.NS": "Nippon India ETF Nifty Pharma BeES", "XYZ.NS": "XYZ Gilt Fund"}
        self.assertTrue(PS.is_company("ABC.NS", names))
        self.assertFalse(PS.is_company("PHARMABEES.NS", names))
        self.assertFalse(PS.is_company("GOLDBEES.NS", {}))
        self.assertFalse(PS.is_company("XYZ.NS", names))


class ScanTests(unittest.TestCase):
    def run_scan(self, rules=None, pledges=None):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        prices = {t: frame(s, v) for t, (s, v, _) in STOCKS.items()}
        prices[PS.BENCH] = frame(series(250, 0.0003), 1e6)
        bhav = pd.DataFrame({"close": [s.iloc[-1] for s, _, _ in STOCKS.values()], "turnover_lakh": 100.0, "deliv_per": 50.0},
                            index=list(STOCKS))
        bhav.attrs["date"] = date(2026, 10, 1)
        fd = {t: f for t, (_, _, f) in STOCKS.items()}
        pledges = pledges or {}
        with mock.patch.object(PS, "load_universe", return_value=prices), \
                mock.patch.object(F, "fetch_many", side_effect=lambda syms, *a, **k: {s: fd[s] for s in syms}), \
                mock.patch.object(F, "pledge", side_effect=lambda t, *a, **k: pledges.get(t, 0.0)):
            return PS.scan(rules or PS.PennyRules(top=5), 200_000, tmp, bhav=bhav)

    def test_each_rule_removes_its_stock(self):
        r = self.run_scan()
        picked = [p["ticker"] for p in r["picks"]]
        self.assertEqual(set(picked), {"GOOD.NS", "OKAY.NS"})
        near = {n["ticker"]: n["rule"] for n in r["near_misses"]}
        self.assertIn("Price above its 200-day average", near["DOWN.NS"])
        self.assertIn("Not up more than", near["PUMP.NS"])
        self.assertNotIn("LOSS.NS", near)                  # a loss maker also fails ROE: two rules, not a near miss
        self.assertIn("Debt no more than", near["DEBT.NS"])
        self.assertIn("Market value at least", near["TINY.NS"])
        self.assertIn("Promoters hold at least", near["PROMO.NS"])
        self.assertNotIn("THIN.NS", near)                  # fails liquidity: not a real candidate
        funnel = {f["label"]: f["count"] for f in r["funnel"]}
        self.assertEqual(funnel["All NSE equities"], 9)
        self.assertEqual(r["passers"], 2)

    def test_ranking_sizing_and_explanations(self):
        r = self.run_scan()
        good, okay = r["picks"]
        self.assertEqual(good["ticker"], "GOOD.NS")                      # more profitable and less borrowed
        self.assertGreater(good["score"], okay["score"])
        self.assertAlmostEqual(r["bucket"], 20_000)
        for p in r["picks"]:
            self.assertLessEqual(p["shares"] * p["price"], r["per_stock"] + 1e-6)
            self.assertEqual(p["amount"], p["shares"] * p["price"])
            self.assertTrue(p["why"])
        self.assertTrue(any("Earns" in w for w in good["why"]))

    def test_pledge_over_the_limit_drops_a_pick_and_a_small_one_is_flagged(self):
        r = self.run_scan(pledges={"GOOD.NS": 0.7, "OKAY.NS": 0.15})
        self.assertEqual([p["ticker"] for p in r["picks"]], ["OKAY.NS"])
        self.assertIn("pledged", r["dropped"][0]["why"])
        self.assertTrue(any("pledged" in f for f in r["picks"][0]["flags"]))

    def test_relaxed_rules_let_more_in(self):
        r = self.run_scan(PS.PennyRules(top=9, min_roe=-1, max_de=9, min_promoter=0, min_mcap_cr=1, uptrend=False))
        picked = {p["ticker"] for p in r["picks"]}
        self.assertTrue({"DEBT.NS", "TINY.NS", "PROMO.NS", "DOWN.NS"} <= picked)
        self.assertNotIn("LOSS.NS", picked)                              # profit is always required
        self.assertNotIn("PUMP.NS", picked)


class BacktestHelperTests(unittest.TestCase):
    def test_outcomes_are_measured_forward_from_the_date_only(self):
        idx = pd.bdate_range("2018-01-01", "2023-06-30")
        cl = pd.DataFrame({"up": np.linspace(10, 40, len(idx)), "down": np.linspace(10, 4, len(idx))}, index=idx)
        out = PS.outcomes(cl, lambda d: cl.columns, [pd.Timestamp("2019-12-31")])
        self.assertEqual(out["n"], 2)
        self.assertEqual(out["share_down"], 0.5)
        self.assertEqual(out["share_lost_half"], 0.0)

    def test_price_score_prefers_steady_risers(self):
        idx = pd.bdate_range("2020-01-01", periods=300)
        fr = {k: pd.DataFrame({"a": v[0], "b": v[1]}, index=idx) for k, v in
              {"ret_12m": (0.8, 0.1), "ret_6m": (0.4, 0.0), "vol_1y": (0.3, 0.9), "value_cr": (2.0, 2.0)}.items()}
        sc = PS.price_score(fr).iloc[-1]
        self.assertGreater(sc["a"], sc["b"])


# ------------------------------------------------------------------------------------------- penny coins
def coin(sym, price, mcap, vol, years=4.0, rank=100, total=0, maxs=0, pct7=2.0, name=None):
    first = (date(2026, 10, 3) - timedelta(days=int(years * 365.25))).isoformat() + "T00:00:00Z"
    return {"id": f"{sym.lower()}-x", "name": name or sym, "symbol": sym, "rank": rank, "total_supply": total, "max_supply": maxs,
            "first_data_at": first, "quotes": {"USD": {"price": price, "volume_24h": vol, "market_cap": mcap, "percent_change_7d": pct7,
                                                       "ath_price": price * 5, "percent_from_price_ath": -80.0}}}


class CryptoTests(unittest.TestCase):
    def test_filters_and_scan(self):
        rows = [coin("GOOD", 0.30, 3e8, 2e7, total=1.1e9, maxs=2e9), coin("PUMPED", 0.4, 3e8, 2e7, total=1e9),
                coin("STABLE", 1.0, 5e8, 9e8, pct7=0.0), coin("WRAP", 0.5, 3e8, 2e7, name="Wrapped Thing"),
                coin("BIGP", 5.0, 3e8, 2e7), coin("TINY", 0.2, 1e7, 2e6), coin("QUIET", 0.2, 3e8, 1e5),
                coin("YOUNG", 0.2, 3e8, 2e7, years=0.5), coin("UNLOCK", 0.2, 3e8, 2e7, total=1e10),
                coin("DOWN", 0.2, 3e8, 2e7, total=1e9), coin("NOHIST", 0.2, 3e8, 2e7, total=1e9)]
        rows += [coin(f"F{i}", 5 + i, 1e9, 1e8) for i in range(110)]       # filler so the list looks real
        coins = PC.from_paprika(rows, date(2026, 10, 3))
        n = 400
        idx = pd.date_range(end="2026-10-03", periods=n)
        mk = lambda a, b: pd.Series(np.linspace(a, b, n), index=idx)
        hist = {"BTC-USD": mk(100, 110), "GOOD-USD": mk(0.15, 0.30), "PUMPED-USD": pd.Series(np.r_[np.full(n - 25, 0.1), np.linspace(0.1, 0.4, 25)], index=idx),
                "UNLOCK-USD": mk(0.1, 0.2), "DOWN-USD": mk(0.4, 0.2), "YOUNG-USD": mk(0.1, 0.2)}
        hist["NOHIST-USD"] = mk(5, 6)                                       # a different coin with this ticker: ignored
        with mock.patch.object(PC, "load_history", return_value=hist):
            r = PC.scan(PC.CryptoRules(top=5), 200_000, Path(tempfile.mkdtemp()), coins=coins, usd_inr=90.0, today=date(2026, 10, 3))
        self.assertEqual([p["symbol"] for p in r["picks"]], ["GOOD"])
        near = {n_["ticker"].replace("-USD", ""): n_["rule"] for n_ in r["near_misses"]}
        self.assertIn("Not up more than", near["PUMPED"])
        self.assertIn("200-day", near["DOWN"])
        self.assertIn("Fully diluted", near["UNLOCK"])
        self.assertIn("years old", near["YOUNG"])
        self.assertIn("history", near["NOHIST"])
        self.assertNotIn("STABLE", near)
        self.assertNotIn("WRAP", near)
        good = r["picks"][0]
        self.assertAlmostEqual(good["units"] * good["price"] * 90, r["bucket"], places=6)
        self.assertGreater(good["rs_180d"], 0)                              # beat Bitcoin
        self.assertIn("India", r["note"])

    def test_history_metrics(self):
        idx = pd.date_range(end="2026-10-03", periods=400)
        s = pd.Series(np.linspace(1, 2, 400), index=idx)
        b = pd.Series(np.linspace(1, 1.2, 400), index=idx)
        m = PC.history_metrics(s, b)
        self.assertAlmostEqual(m["ret_365d"], 2 / s.iloc[-366] - 1, places=6)
        self.assertGreater(m["vs_sma200"], 0)
        self.assertAlmostEqual(m["dd_52w"], 0.0)
        self.assertGreater(m["rs_180d"], 0)
        self.assertEqual(PC.history_metrics(s.iloc[:10], b), {})

    def test_coin_list_falls_back_to_coingecko(self):
        gecko = [[{"id": f"c{i}", "symbol": f"c{i}", "name": "Coin", "market_cap_rank": i, "current_price": 0.5, "market_cap": 1e8,
                   "total_volume": 5e6, "total_supply": 1e9, "max_supply": None, "circulating_supply": 5e8, "ath": 2.0,
                   "ath_change_percentage": -75.0} for i in range(1, 130)]]
        ok = mock.Mock(json=lambda: gecko[0], raise_for_status=lambda: None)
        with mock.patch.object(PC.requests, "get", side_effect=[RuntimeError("down"), ok, ok]):
            df, source = PC.fetch_coins(date(2026, 10, 3))
        self.assertEqual(source, "CoinGecko")
        self.assertEqual(len(df), 258)                                       # two pages of 129


# ------------------------------------------------------------------------------------------- the automatic run
class AutomaticRunTests(unittest.TestCase):
    def setUp(self):
        from stock_agent import app

        self.app = app
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        for name, f in (("PENNY_FILE", "penny.json"), ("SETTINGS_FILE", "settings.json"), ("ALERTS_FILE", "alerts.json")):
            p = mock.patch.object(app, name, self.tmp / f)
            p.start()
            self.addCleanup(p.stop)

    def result(self, tickers):
        return {"as_of": "2026-10-01", "picks": [{"ticker": t} for t in tickers]}

    def test_changes_are_against_the_previous_day_and_days_on_list_count(self):
        app = self.app
        evening = datetime(2026, 10, 1, 17, 0, tzinfo=IST)
        r1 = app._penny_save("stocks", self.result(["A.NS", "B.NS"]), date(2026, 10, 1), evening)
        self.assertTrue(r1["changes"]["first"])
        r1b = app._penny_save("stocks", self.result(["A.NS", "B.NS", "C.NS"]), date(2026, 10, 1), evening)   # same day: still the first baseline
        self.assertTrue(r1b["changes"]["new"] == [] and not r1b["changes"]["out"])
        r2 = app._penny_save("stocks", self.result(["B.NS", "C.NS", "D.NS"]), date(2026, 10, 2), evening)
        self.assertEqual(r2["changes"]["new"], ["D.NS"])                                                      # against 1 Oct's list (A, B, C)
        self.assertEqual(r2["changes"]["out"], ["A.NS"])
        by = {p["ticker"]: p for p in r2["picks"]}
        self.assertEqual(by["B.NS"]["days_on_list"], 2)
        self.assertEqual(by["D.NS"]["days_on_list"], 1)
        self.assertEqual(self.app.penny_state()["stocks"]["picks"][0]["ticker"], "B.NS")

    def test_due_only_after_a_first_scan_and_after_the_close(self):
        app = self.app
        mid = datetime(2026, 10, 1, 11, 0, tzinfo=IST)
        evening = datetime(2026, 10, 1, 17, 0, tzinfo=IST)
        saturday = datetime(2026, 10, 3, 17, 0, tzinfo=IST)
        self.assertEqual(app._penny_due(evening), [])                       # nothing scanned yet: never starts by itself
        app._penny_save("stocks", self.result(["A.NS"]), date(2026, 9, 30), datetime(2026, 9, 30, 11, 0, tzinfo=IST))
        app._penny_save("crypto", self.result(["X-USD"]), date(2026, 9, 30), datetime(2026, 9, 30, 11, 0, tzinfo=IST))
        self.assertEqual(app._penny_due(mid), ["crypto"])                   # coins once a day, stocks wait for the close
        self.assertEqual(app._penny_due(evening), ["stocks", "crypto"])
        self.assertEqual(app._penny_due(saturday), ["crypto"])
        app._write_json(app.SETTINGS_FILE, {"penny": {"auto": False}})
        self.assertEqual(app._penny_due(evening), [])

    def test_automatic_run_raises_an_alert_for_new_names_only(self):
        app = self.app
        from stock_agent import alerts as AL

        first = {"picks": [], "changes": {"first": True, "new": [], "out": []}}
        later = {"picks": [], "changes": {"first": False, "new": ["NEW.NS"], "out": ["OLD.NS"]}}
        with mock.patch.object(app, "run_penny_stocks", side_effect=[first, later]), mock.patch.object(AL, "deliver", return_value=[]):
            app.run_penny_auto("stocks")
            self.assertEqual(AL.Store(app.ALERTS_FILE).load()["items"], [])
            app.run_penny_auto("stocks")
        items = AL.Store(app.ALERTS_FILE).load()["items"]
        self.assertEqual(len(items), 1)
        self.assertIn("New: NEW", items[0]["text"])
        self.assertIn("Dropped: OLD", items[0]["text"])

    def test_a_failed_automatic_run_is_recorded_and_not_repeated_today(self):
        app = self.app
        with mock.patch.object(app, "run_penny_stocks", side_effect=RuntimeError("NSE said no")):
            app.run_penny_auto("stocks")
        st = app.penny_state()
        self.assertIn("NSE said no", st["error"]["stocks"])
        self.assertEqual(app._read_json(app.PENNY_FILE, {})["auto"]["stocks"], date.today().isoformat())

    def test_settings_are_checked_and_remembered(self):
        app = self.app
        app._penny_remember("stocks", {"price_cap": 20, "top": 99, "evil": 1})
        s = app._read_json(app.SETTINGS_FILE, {})["penny"]
        self.assertEqual(s["stocks"], {"price_cap": 20.0, "top": 25})
        rules = app._penny_rules("stocks", app.load_settings(), {"min_lakh": 100})
        self.assertEqual((rules.price_cap, rules.min_lakh, rules.top), (20.0, 100.0, 25))


if __name__ == "__main__":
    unittest.main()
