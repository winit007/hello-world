"""Phone app build: static page, data files, and the JavaScript ports of the planner and rebalancer."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stock_agent import app, planner, publish
from stock_agent import rebalance as R

STATS = {
    "equity": {"expected": 0.12, "vol": 0.16}, "gold": {"expected": 0.09, "vol": 0.14},
    "debt": {"expected": 0.07, "vol": 0.02}, "crypto": {"expected": 0.105, "vol": 0.70},
    "corr_equity_gold": -0.05, "corr": {"equity-gold": -0.05, "crypto-equity": 0.25, "crypto-gold": 0.1},
}
INDEX = Path(app.WEB) / "index.html"


def static_js() -> str:
    html = INDEX.read_text(encoding="utf-8")
    start = html.index("/* ---------------- phone version (static site)")
    end = html.index("async function staticBoot()")
    return html[start:end]


def run_js(expr: str):
    code = "const $ = () => null, $$ = () => [], esc = s => s;\n" + static_js() + f"\nconsole.log(JSON.stringify({expr}));"
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=120)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


class BuildTests(unittest.TestCase):
    def test_windows_bundle_lists_every_module(self):
        pkg = Path(app.__file__).parent
        mods = {f.stem for f in pkg.glob("*.py") if not f.stem.startswith("_")}
        listed = set(re.findall(r"^from \. import (\w+)$", (pkg / "_bundle.py").read_text(), re.M))
        self.assertEqual(mods - listed, set(), "add new modules to stock_agent/_bundle.py")
        import stock_agent._bundle  # noqa: F401  (every listed module imports cleanly)

    def test_every_screener_measure_is_explained(self):
        from stock_agent import myscreen
        html = INDEX.read_text(encoding="utf-8")
        block = html[html.index("const MS_HELP = {"):html.index("const MS_PRESETS = {")]
        self.assertEqual(set(re.findall(r"^  (\w+): \[", block, re.M)), set(myscreen.METRICS))
        self.assertIn('id="ms-help"', html)

    def test_benchmark_choices_match(self):
        from stock_agent.attribution import BENCHMARKS
        html = INDEX.read_text(encoding="utf-8")
        for sel in ("f-bench", "ms-bench"):
            block = html[html.index(f'id="{sel}"'):]
            block = block[:block.index("</select>")]
            self.assertEqual(re.findall(r'option value="([^"]+)"', block), list(BENCHMARKS), sel)

    def test_static_page_uses_relative_paths_and_static_flag(self):
        html = publish.page_html(app.WEB)
        self.assertIn('<meta name="static-site" content="1">', html)
        self.assertNotIn("__AGENT_TOKEN__", html)
        self.assertNotIn('href="/manifest.webmanifest"', html)

    def test_build_writes_data_and_survives_a_failing_step(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        boom = mock.Mock(side_effect=RuntimeError("Yahoo said no"))
        with mock.patch.object(app, "HOME", tmp / "home"), \
                mock.patch.object(app, "run_screen", return_value={"picks": [], "stats": {}}), \
                mock.patch.object(app, "run_market", side_effect=lambda m, s, p: {"market": m, "rows": []}), \
                mock.patch.object(app, "run_fund", boom):
            meta = publish.build(tmp / "site", steps=["screen", "crypto", "commodities", "fund"], log=lambda *a: None)
        site = tmp / "site"
        self.assertEqual(meta["ok"], ["screen", "crypto", "commodities"])
        self.assertIn("Yahoo said no", meta["errors"]["fund"])
        self.assertEqual(json.loads((site / "data" / "commodities.json").read_text())["market"], "commodities")
        for f in ("index.html", "sw.js", "manifest.webmanifest", "icon-192.png", "icon-512.png", "data/meta.json", "termux.sh"):
            self.assertTrue((site / f).exists(), f)
        man = json.loads((site / "manifest.webmanifest").read_text())
        self.assertEqual((man["start_url"], man["icons"][0]["src"]), ("./", "icon-192.png"))
        self.assertEqual(json.loads((site / "data" / "meta.json").read_text())["settings"]["universe"], "fno")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class JsPortTests(unittest.TestCase):
    """The phone computes Goals and Rebalance in JavaScript; results must match the Python originals."""

    def test_allocation_matches(self):
        for years, profile, crypto in [(2, "aggressive", 0.1), (4, "balanced", 0), (8, "balanced", 0.05),
                                       (15, "aggressive", 0.2), (6, "conservative", 0.5)]:
            js = run_js(f"goalAllocation({years}, '{profile}', {crypto})")
            self.assertEqual(js, planner.allocation(years, profile, crypto), (years, profile, crypto))

    def test_goal_plan_matches_monte_carlo(self):
        g = planner.Goal(target_today=3_000_000, years=12, current=200_000, monthly=15_000, crypto=0.05)
        py = planner.plan(g, stats=STATS)
        body = json.dumps({"target_today": 3_000_000, "years": 12, "current": 200_000, "monthly": 15_000, "crypto": 0.05})
        js = run_js(f"goalPlan({body}, {json.dumps(STATS)})")
        self.assertEqual(js["mix"], py["mix"])
        self.assertAlmostEqual(js["target_future"], py["target_future"], places=0)
        self.assertAlmostEqual(js["expected_return"], py["expected_return"], places=9)
        self.assertAlmostEqual(js["invested"], py["invested"], places=0)
        self.assertAlmostEqual(js["probability"], py["probability"], delta=0.03)       # different random draws
        self.assertAlmostEqual(js["median"] / py["median"], 1, delta=0.03)
        self.assertAlmostEqual(js["p10"] / py["p10"], 1, delta=0.05)
        self.assertAlmostEqual(js["needed_monthly_75"] / py["needed_monthly_75"], 1, delta=0.05)
        self.assertEqual(len(js["bands"]), len(py["bands"]))
        self.assertAlmostEqual(js["compare_without_crypto"]["probability"], py["compare_without_crypto"]["probability"], delta=0.03)
        self.assertEqual(js["monthly_split"], py["monthly_split"])

    def test_rebalance_matches(self):
        prices = {"NIFTYBEES.NS": 280.0, "BTC-USD": 60_000.0, "USDINR=X": 90.0}
        holdings = [{"name": "Nifty ETF", "asset_class": "equity", "symbol": "NIFTYBEES.NS", "quantity": 2000},
                    {"name": "FD", "asset_class": "debt", "value": 150_000},
                    {"name": "Bitcoin", "asset_class": "crypto", "symbol": "BTC-USD", "quantity": 0.05},
                    {"name": "Odd", "asset_class": "weird", "value": 10_000}]
        target = {"equity": 60, "debt": 30, "gold": 10}
        for band, new_money in [(5, 0), (5, 100_000), (50, 20_000)]:
            body = {"holdings": holdings, "target": target, "band": band, "new_money": new_money}
            js = run_js(f"rebalancePlan({json.dumps(body)}, {json.dumps(prices)})")
            priced = []
            for h in holdings:
                px = prices.get(h.get("symbol"))
                inr = None if px is None else (px if h["symbol"].endswith(".NS") else px * 90.0)
                priced.append({"name": h["name"], "asset_class": h["asset_class"] if h["asset_class"] in R.CLASSES else "other",
                               "symbol": h.get("symbol"), "price": inr,
                               "value": float(h.get("value") or (inr * h["quantity"]))})
            py = R.recommend(priced, target, band / 100, new_money)
            self.assertEqual(js["verdict"], py["verdict"])
            self.assertEqual(js["needed"], py["needed"])
            self.assertEqual([r["asset_class"] for r in js["rows"]], [r["asset_class"] for r in py["rows"]])
            for a, b in zip(js["rows"], py["rows"]):
                self.assertAlmostEqual(a["trade"], b["trade"], places=2)
            self.assertEqual([(s["name"], s["quantity"]) for s in js["sells"]], [(s["name"], s["quantity"]) for s in py["sells"]])
            self.assertEqual(js["new_split"].keys(), py["new_split"].keys())
            for k in py["new_split"]:
                self.assertAlmostEqual(js["new_split"][k], py["new_split"][k], places=1)

    def test_journal_on_phone_books_pnl(self):
        js = run_js("""(() => { const mem = {}; globalThis.localStorage = { getItem: k => mem[k] ?? null, setItem: (k, v) => { mem[k] = v; } };
            const a = journalLocal("POST", "/api/journal", { ticker: "TCS.NS", lots: 2, lot_size: 175, entry_premium: 40 });
            const id = a.entries[0].id;
            return journalLocal("POST", "/api/journal/" + id, { exit_premium: 52 }); })()""")
        self.assertEqual(js["entries"][0]["pnl"], (52 - 40) * 175 * 2)
        self.assertEqual(js["summary"]["win_rate"], 1)


if __name__ == "__main__":
    unittest.main()
