"""Company results for My screener: publication lag, ratios, promoter holding and pledges. No network."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from stock_agent import fundamentals as F
from stock_agent import myscreen as M

DATA = {"annual": {
    "2023-03-31": {"eps": 10.0, "ni": 1e9, "revenue": 1e10, "ebit": 1.5e9, "equity": 5e9, "debt": 1e9, "shares": 1e8, "dividends": -2e8},
    "2024-03-31": {"eps": 12.0, "ni": 1.2e9, "revenue": 1.1e10, "ebit": 1.8e9, "equity": 6e9, "debt": 1e9, "shares": 1e8, "dividends": -3e8}},
    "shareholding": [{"published": "2023-07-15", "promoter": 0.60, "xbrl": None},
                     {"published": "2024-07-15", "promoter": 0.62, "xbrl": "https://example/x.xml"}]}


def closes():
    idx = pd.bdate_range("2022-01-01", "2025-06-30")
    return pd.DataFrame({"AAA.NS": 200.0, "BBB.NS": 100.0}, index=idx)


class FundamentalsTests(unittest.TestCase):
    def test_results_count_only_after_publication(self):
        fr = F.frames(closes(), {"AAA.NS": DATA})
        pe = fr["pe"]["AAA.NS"]
        self.assertTrue(pe.loc[:"2023-05-29"].isna().all())               # FY23 published by 30 May
        self.assertAlmostEqual(pe.loc["2023-06-01"], 200 * 1e8 / 1e9)       # market value / net profit = 20
        self.assertAlmostEqual(pe.loc["2024-06-03"], 200 * 1e8 / 1.2e9)
        self.assertAlmostEqual(fr["eps_growth"]["AAA.NS"].loc["2024-06-03"], 0.2)
        self.assertAlmostEqual(fr["roe"]["AAA.NS"].loc["2024-06-03"], 1.2e9 / 5.5e9)   # profit / average equity
        self.assertAlmostEqual(fr["de"]["AAA.NS"].loc["2024-06-03"], 1 / 6)
        self.assertAlmostEqual(fr["div_yield"]["AAA.NS"].loc["2024-06-03"], 3e8 / 2e10)
        self.assertTrue(fr["pe"]["BBB.NS"].isna().all())                   # no data: nothing invented

    def test_promoter_holding_and_change(self):
        fr = F.frames(closes(), {"AAA.NS": DATA})
        pr, ch = fr["promoter"]["AAA.NS"], fr["promoter_chg"]["AAA.NS"]
        self.assertTrue(np.isnan(pr.loc["2023-07-14"]))
        self.assertAlmostEqual(pr.loc["2023-07-17"], 0.60)
        self.assertAlmostEqual(ch.loc["2024-07-16"], 0.02)

    def test_loss_makers_have_no_pe(self):
        d = {"annual": {"2024-03-31": {**DATA["annual"]["2024-03-31"], "ni": -1e8}}}
        self.assertTrue(F.frames(closes(), {"AAA.NS": d})["pe"]["AAA.NS"].isna().all())

    def test_pledge_from_filing(self):
        xml = ('<x:NumberOfShares contextRef="ShareholdingOfPromoterAndPromoterGroup_ContextI" unitRef="shares">1000</x:NumberOfShares>'
               '<x:NumberOfSharesEncumberedUnderPledged contextRef="Indian_ContextI" unitRef="shares">250</x:NumberOfSharesEncumberedUnderPledged>')
        self.assertAlmostEqual(F.pledge_from_xbrl(xml), 0.25)
        self.assertEqual(F.pledge_from_xbrl(xml.replace("250", "0")), 0.0)
        self.assertIsNone(F.pledge_from_xbrl("<nothing/>"))

    def test_fetch_caches_and_survives_failures(self):
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(F, "_yahoo", return_value=DATA["annual"]) as y, \
                mock.patch.object(F, "_nse_master", side_effect=RuntimeError("NSE blocked")):
            a = F.fetch("AAA.NS", tmp)
            b = F.fetch("AAA.NS", tmp)
        self.assertEqual(a["annual"], DATA["annual"])
        self.assertEqual(a["shareholding"], [])
        self.assertEqual(b, a)
        self.assertEqual(y.call_count, 1)                                  # second call read the cache

    def test_screen_with_company_rules_starts_when_results_exist(self):
        rng = np.random.default_rng(4)
        idx = pd.bdate_range("2019-01-01", "2025-06-30")
        cols = [f"S{i}.NS" for i in range(12)]
        cl = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0.0005, 0.015, (len(idx), 12)), axis=0), index=idx, columns=cols)
        data = {t: {"annual": {y: {**v, "ni": v["ni"] * (1 + i / 10)} for y, v in DATA["annual"].items()}} for i, t in enumerate(cols)}
        sc = M.Screen(filters=[{"metric": "roe", "op": ">", "value": 0.2}], rank_by="roe", top=3, rebalance="Q")
        r = M.run(cl, cl * 1e6, cl.mean(axis=1), sc, fund_data=data)
        self.assertEqual(r["backtest"]["fundamentals_from"][:7], "2023-06")
        self.assertTrue(all(p["roe"] > 0.2 for p in r["picks"]))
        with self.assertRaises(ValueError):
            M.run(cl, cl * 1e6, cl.mean(axis=1), sc)                       # company rules need company data


if __name__ == "__main__":
    unittest.main()
