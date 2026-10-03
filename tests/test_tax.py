"""Capital-gains tax and harvesting ideas."""
import unittest
from datetime import date

from stock_agent import tax as T


class TaxTests(unittest.TestCase):
    def test_rates_exemption_and_set_off(self):
        self.assertAlmostEqual(T.tax(100_000, 0)["tax"], 20_000)
        self.assertAlmostEqual(T.tax(0, 125_000)["tax"], 0)                     # inside the exemption
        self.assertAlmostEqual(T.tax(0, 225_000)["tax"], 12_500)
        # short-term loss reduces long-term gains too; long-term loss cannot touch short-term gains
        self.assertAlmostEqual(T.tax(-50_000, 225_000)["tax"], 50_000 * 0.125)
        r = T.tax(100_000, -40_000)
        self.assertAlmostEqual(r["tax"], 20_000)
        self.assertAlmostEqual(r["carry_lt"], 40_000)
        self.assertAlmostEqual(T.tax(100_000, 0, cf_st=30_000)["tax"], 14_000)

    def test_holding_period_and_financial_year(self):
        self.assertFalse(T.long_term(date(2024, 1, 10), date(2025, 1, 10)))      # exactly 12 months: short
        self.assertTrue(T.long_term(date(2024, 1, 10), date(2025, 1, 11)))
        self.assertEqual(T.fy_bounds(date(2026, 3, 31)), (date(2025, 4, 1), date(2026, 3, 31)))
        self.assertEqual(T.fy_bounds(date(2026, 4, 1))[0], date(2026, 4, 1))

    def test_plan_ideas(self):
        today = date(2026, 2, 1)
        holdings = [
            {"id": "a", "symbol": "LOSER.NS", "qty": 100, "buy_price": 500, "buy_date": "2025-09-01"},     # -₹20,000 short-term
            {"id": "b", "symbol": "OLDWIN.NS", "qty": 100, "buy_price": 1000, "buy_date": "2023-01-01"},   # +₹1,00,000 long-term
            {"id": "c", "symbol": "SOON.NS", "qty": 10, "buy_price": 100, "buy_date": "2025-03-01"},       # long-term from 2 Mar
        ]
        prices = {"LOSER.NS": 300.0, "OLDWIN.NS": 2000.0, "SOON.NS": 150.0}
        sales = [{"symbol": "X.NS", "qty": 10, "price": 2000, "date": "2025-06-01", "buy_price": 1000, "buy_date": "2025-01-01"}]
        p = T.plan(holdings, prices, sales, today=today)
        self.assertAlmostEqual(p["realised_st"], 10_000)
        self.assertAlmostEqual(p["tax_now"]["tax"], 2_000)
        kinds = {i["kind"]: i for i in p["ideas"]}
        self.assertAlmostEqual(kinds["loss"]["tax_saved"], 2_000)            # wipes out the short-term tax
        self.assertGreater(kinds["loss"]["carry_forward"], 0)                # and leaves loss to carry forward
        none = T.plan(holdings[:1], prices, [], today=today)                   # no gains to offset: no loss idea
        self.assertFalse([i for i in none["ideas"] if i["kind"] == "loss"])
        self.assertEqual(kinds["gain"]["qty"], 100)                          # ₹1,00,000 fits the ₹1.25 lakh exemption
        self.assertAlmostEqual(kinds["gain"]["tax_saved"], 12_500)
        self.assertEqual(kinds["wait"]["symbol"], "SOON.NS")
        self.assertEqual(p["days_left"], 58)
        self.assertEqual(p["fy"], "2025-26")


if __name__ == "__main__":
    unittest.main()
