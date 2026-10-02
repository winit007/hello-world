import unittest
from datetime import date, datetime

from stock_agent import checklist as C


def _ev(**kw):
    base = dict(edge_ok=True, edge_detail="ok", stop=95.0, target=110.0, time_exit="08 Oct", max_loss=3000,
                budget=4000, affordable=True)
    base.update(kw)
    return C.evaluate(**base)


class ChecklistTests(unittest.TestCase):
    def status(self, cl, key):
        return next(i["status"] for i in cl["items"] if i["id"] == key)

    def test_all_pass_needs_manual_items_confirmed(self):
        cl = _ev(spread_pct=0.01, results_known=True, results_date=date(2026, 11, 1), exit_date=date(2026, 10, 8))
        self.assertEqual(self.status(cl, "events"), "check")    # RBI/budget can only be confirmed by you
        self.assertFalse(cl["ok"])
        self.assertEqual(cl["failed"], 0)
        self.assertEqual(self.status(cl, "timing"), "na")

    def test_failures(self):
        self.assertEqual(self.status(_ev(edge_ok=False), "edge"), "fail")
        self.assertEqual(self.status(_ev(max_loss=5000), "risk"), "fail")
        self.assertEqual(self.status(_ev(affordable=False, max_loss=None), "risk"), "fail")
        self.assertEqual(self.status(_ev(spread_pct=0.05), "liquid"), "fail")
        cl = _ev(results_known=True, results_date=date(2026, 10, 8), exit_date=date(2026, 10, 8))
        self.assertEqual(self.status(cl, "events"), "fail")
        self.assertIn("Do not trade", cl["verdict"])

    def test_intraday_timing(self):
        at = lambda h, m, d=5: datetime(2026, 10, d, h, m, tzinfo=C.IST)
        self.assertEqual(self.status(_ev(intraday=True, now=at(9, 20)), "timing"), "fail")      # first 15 minutes
        self.assertEqual(self.status(_ev(intraday=True, now=at(11, 0)), "timing"), "pass")
        self.assertEqual(self.status(_ev(intraday=True, now=at(15, 0)), "timing"), "fail")      # under 30 min left
        self.assertEqual(self.status(_ev(intraday=True, now=at(11, 0, d=4)), "timing"), "fail")  # Sunday
        self.assertEqual(self.status(_ev(intraday=True, session="mcx", now=at(21, 0)), "timing"), "pass")

    def test_short_line(self):
        line = C.short_line(_ev(edge_ok=False))
        self.assertTrue(line.startswith("CHECKLIST ✕edge ✓exit ✓risk"))


if __name__ == "__main__":
    unittest.main()
