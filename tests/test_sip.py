import unittest

import numpy as np
import pandas as pd

from stock_agent import sip as S


def steady(rate, start="2018-01-01", end="2026-09-30"):
    idx = pd.bdate_range(start, end)
    return pd.Series(np.power(1 + rate, (idx - idx[0]).days / 365.25), index=idx)


class SipTests(unittest.TestCase):
    def test_xirr_of_a_known_cash_flow(self):
        d0, d1 = pd.Timestamp("2020-01-01"), pd.Timestamp("2021-01-01")
        self.assertAlmostEqual(S.xirr([(d0, -100), (d1, 110)]), 1.1 ** (365.25 / 366) - 1, places=6)
        self.assertIsNone(S.xirr([(d0, -100), (d1, -5)]))

    def test_steady_growth_gives_the_same_rate_every_way(self):
        g = steady(0.12)
        r = S.report(g, steady(0.08))
        for k in ("sip", "lump"):
            self.assertAlmostEqual(r[k]["strategy"]["xirr"], 0.12, places=4)
            self.assertAlmostEqual(r[k]["benchmark"]["xirr"], 0.08, places=4)
        self.assertEqual(r["sip"]["strategy"]["invested"], r["sip"]["strategy"]["months"])
        # parked in a 6.5% liquid fund while it moves in: a bit below the lump sum, more so over longer
        self.assertLess(r["stp"]["24"]["strategy"]["xirr"], r["stp"]["6"]["strategy"]["xirr"])
        self.assertLess(r["stp"]["6"]["strategy"]["xirr"], 0.12)
        self.assertEqual(r["rolling"]["beat_share"], 1.0)
        self.assertAlmostEqual(r["rolling"]["strategy"]["median"], 0.12, places=4)
        last = r["curve"][-1]
        self.assertAlmostEqual(last["strategy"], r["sip"]["strategy"]["value"], places=6)

    def test_sip_softens_a_crash_at_the_start(self):
        idx = pd.bdate_range("2019-01-01", "2023-12-29")
        path = np.where(np.arange(len(idx)) < 250, np.linspace(1, 0.5, len(idx)), 0.5)
        nav = pd.Series(path, index=idx)
        nav.iloc[250:] = np.linspace(0.5, 1.0, len(idx) - 250)
        r = S.report(nav, nav)
        self.assertGreater(r["sip"]["strategy"]["xirr"], r["lump"]["strategy"]["xirr"])    # bought cheap units


if __name__ == "__main__":
    unittest.main()
