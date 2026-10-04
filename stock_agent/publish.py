"""Build the phone app: a static copy of the app plus the evening's results as data files.

    python -m stock_agent publish --out _site

GitHub Actions runs this every weekday evening and hosts `_site` on GitHub Pages, so the phone needs no PC.
The page is the same index.html in "static mode": scans are read from data/*.json, Goals and Rebalance
are computed on the phone, and features that need a live server (stock lookup, paper trading, Kite) are
hidden. Each step is independent: if one fails, the others are still published and the error is listed
in data/meta.json.
"""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
SW = """// Network first, so new results show as soon as they are published; the last copy works offline.
const CACHE = "stock-agent-v1";
self.addEventListener("install", e => { self.skipWaiting(); e.waitUntil(caches.open(CACHE).then(c => c.addAll(["./", "index.html", "manifest.webmanifest", "icon-192.png"]))); });
self.addEventListener("activate", e => { e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", e => {
  if (e.request.method !== "GET" || new URL(e.request.url).origin !== location.origin) return;
  e.respondWith(fetch(e.request).then(r => { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); return r; })
    .catch(() => caches.match(e.request, { ignoreSearch: true })));
});
"""
PRICE_EXTRA = ["NIFTYBEES.NS", "GOLDBEES.NS", "JUNIORBEES.NS", "BANKBEES.NS", "LIQUIDBEES.NS", "SILVERBEES.NS",
               "MON100.NS", "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD", "DOGE-USD", "USDINR=X"]


def _quiet(msg, frac):
    pass


def page_html(web_dir: Path) -> str:
    """index.html switched to static mode, with paths relative to the Pages folder."""
    html = (web_dir / "index.html").read_text(encoding="utf-8")
    html = html.replace('<meta name="agent-token" content="__AGENT_TOKEN__">',
                        '<meta name="agent-token" content="">\n<meta name="static-site" content="1">')
    html = html.replace('href="/manifest.webmanifest"', 'href="manifest.webmanifest"')
    html = html.replace('href="/apple-touch-icon.png"', 'href="apple-touch-icon.png"')
    return html


def build(out: Path, steps: list[str] | None = None, log=print) -> dict:
    from . import app

    out = Path(out)
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)
    app.HOME.mkdir(parents=True, exist_ok=True)
    settings = {**app.DEFAULT_SETTINGS, "universe": "fno"}
    meta = {"generated": datetime.now(IST).isoformat(timespec="minutes"), "settings": settings, "ok": [], "errors": {}}

    def write(name, obj):
        (data / f"{name}.json").write_text(json.dumps(app.clean(obj), separators=(",", ":")), encoding="utf-8")

    def stats_and_prices(progress):
        from . import planner
        from .data import _cache_path, read_cached, refresh_many
        from .universes import get_universe

        write("goal_stats", planner.market_stats(False, app.CACHE))
        syms = sorted(set(get_universe("nifty100")) | set(PRICE_EXTRA))
        refresh_many(syms, "1mo", "1d", app.CACHE)
        out = {}
        for s in syms:     # latest close, for pricing holdings on the Rebalance tab
            try:
                out[s] = round(float(read_cached(_cache_path(app.CACHE, s, "1mo", "1d"))["Close"].iloc[-1]), 4)
            except Exception:
                pass
        return out

    jobs = {
        "robust": lambda: app.run_robust(settings, _quiet),       # first: today's picks use its filter
        "screen": lambda: app.run_screen(settings, _quiet),
        "track": lambda: app.run_track(settings, 10, _quiet),
        "crypto": lambda: app.run_market("crypto", settings, _quiet),
        "commodities": lambda: app.run_market("commodities", settings, _quiet),
        "fund": lambda: app.run_fund({}, settings, _quiet),
        "longterm": lambda: app.run_longterm(settings, _quiet),
        "ipo": lambda: app.run_ipo(365, _quiet),
        "penny": lambda: app.run_penny_stocks({}, settings, _quiet),
        "pennycrypto": lambda: app.run_penny_crypto({}, settings, _quiet),
        "prices": lambda: stats_and_prices(_quiet),
    }
    for name, fn in jobs.items():
        if steps and name not in steps:
            continue
        t0 = time.time()
        log(f"[{name}] running…")
        try:
            write(name, fn())
            meta["ok"].append(name)
            log(f"[{name}] done in {time.time() - t0:.0f}s")
        except Exception as exc:
            traceback.print_exc()
            meta["errors"][name] = f"{type(exc).__name__}: {exc}"
            log(f"[{name}] FAILED: {exc}")

    (out / "index.html").write_text(page_html(app.WEB), encoding="utf-8")
    manifest = {**app.MANIFEST, "start_url": "./", "scope": "./",
                "icons": [{**i, "src": i["src"].lstrip("/")} for i in app.MANIFEST["icons"]]}
    (out / "manifest.webmanifest").write_text(json.dumps(manifest), encoding="utf-8")
    for size, name in ((192, "icon-192.png"), (512, "icon-512.png"), (180, "apple-touch-icon.png")):
        (out / name).write_bytes(app._icon_png(size))
    (out / "sw.js").write_text(SW, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    installer = Path(__file__).resolve().parent.parent / "termux" / "install.sh"
    if installer.exists():   # the full app for Android: curl -fsSL <site>/termux.sh | bash
        (out / "termux.sh").write_text(installer.read_text(encoding="utf-8"), encoding="utf-8")
    (data / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta


def main(out: str, steps: list[str] | None = None) -> int:
    meta = build(Path(out), steps)
    print(f"Published {len(meta['ok'])} of {len(meta['ok']) + len(meta['errors'])} parts to {out}")
    for k, v in meta["errors"].items():
        print(f"  {k}: {v}")
    # fail the workflow only when nothing useful was produced
    return 0 if meta["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "_site"))
