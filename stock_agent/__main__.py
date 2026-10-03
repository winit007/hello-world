"""Command line entry point.

    python -m stock_agent research AAPL MSFT        # full report per ticker
    python -m stock_agent rules AAPL --period 10y   # only the ranked candlestick rules
    python -m stock_agent news AAPL                 # only headlines + sentiment
    python -m stock_agent trade AAPL TSLA NVDA      # one line each: BUY CALL / BUY PUT / NO TRADE
    python -m stock_agent app                       # point-and-click app in your browser
    python -m stock_agent shortcut                  # desktop launcher for the app
    python -m stock_agent screen                    # top 5 Nifty 50 setups by success rate + news
    python -m stock_agent screen --universe us      # same for US mega caps
    python -m stock_agent track --replay 10 --brief # what-if P&L of the picks, day by day
    python -m stock_agent ipo --days 90             # new NSE listings and IPO news
    python -m stock_agent research AAPL --offline   # use cached prices/news, no network
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import llm, options, screener, sizing, tracker
from .universes import CHOICES, get_universe, index_symbol

DEFAULT_LEDGER = Path.home() / ".stock_agent" / "track.json"
from .backtest import DEFAULT_HORIZONS, evaluate_rules, pool_rules, rank_rules
from .data import DEFAULT_CACHE, load_prices
from .news import load_news, summarize
from .report import build_outlook, render_markdown


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("tickers", nargs="+", help="ticker symbols, e.g. AAPL RELIANCE.NS")
    p.add_argument("--period", default="5y", help="history to analyse (yfinance period, default 5y)")
    p.add_argument("--interval", default="1d", help="bar size (1d, 1wk, 1h ...)")
    p.add_argument("--offline", action="store_true", help="never touch the network; use the local cache")
    p.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    p.add_argument("--horizons", default=",".join(map(str, DEFAULT_HORIZONS)), help="holding periods in bars")
    p.add_argument("--min-samples", type=int, default=10, help="occurrences a rule needs before it is ranked")
    p.add_argument("--no-context", action="store_true", help="ignore the prior-trend requirement of patterns")


COLS = ["pattern", "direction", "horizon", "n", "win_rate", "baseline", "edge", "avg_return", "profit_factor", "wilson_lb"]
FMT = {"win_rate": "{:.1%}".format, "baseline": "{:.1%}".format, "edge": "{:+.1%}".format,
       "avg_return": "{:+.2%}".format, "wilson_lb": "{:.1%}".format, "profit_factor": "{:.2f}".format}


def cmd_rules(args) -> int:
    horizons = [int(h) for h in args.horizons.split(",")]
    per_ticker = {}
    for t in args.tickers:
        df = load_prices(t, args.period, args.interval, args.offline, args.cache_dir)
        rules = evaluate_rules(df, horizons=horizons, use_context=not args.no_context)
        per_ticker[t] = rules
        if args.pooled:
            continue
        ranked = rank_rules(rules, args.min_samples)
        print(f"\n=== {t}: {len(df)} bars, {df.index[0].date()} -> {df.index[-1].date()} ===")
        print(ranked[COLS].head(args.top).to_string(index=False, formatters=FMT))
    if args.pooled:
        ranked = rank_rules(pool_rules(per_ticker), args.min_samples)
        print(f"\n=== pooled across {len(per_ticker)} tickers: {', '.join(per_ticker)} ===")
        print(ranked[COLS + ["tickers"]].head(args.top).to_string(index=False, formatters=FMT))
    return 0


def cmd_news(args) -> int:
    for t in args.tickers:
        heads = load_news(t, args.company, args.offline, args.cache_dir)
        s = summarize(heads)
        print(f"\n=== {t}: {s['count']} headlines, mean sentiment {s['mean']:+.2f} "
              f"({s['positive']}+ / {s['neutral']}0 / {s['negative']}-) ===")
        for h in heads[:args.top]:
            print(f"{h.published} {h.sentiment:+.2f} {h.label:8} {h.title}  [{h.themes}]")
    return 0


def cmd_trade(args) -> int:
    """One line per ticker: BUY CALL / BUY PUT / NO TRADE, with the contract and its historical odds."""
    horizons = [int(h) for h in args.horizons.split(",")]
    if args.horizon not in horizons:
        horizons.append(args.horizon)
    for t in args.tickers:
        df = load_prices(t, args.period, args.interval, args.offline, args.cache_dir)
        rules = evaluate_rules(df, horizons=horizons, use_context=not args.no_context)
        heads = load_news(t, args.company, args.offline, args.cache_dir)
        outlook = build_outlook(t, df, rules, heads, horizon=args.horizon, lookback=args.lookback, min_samples=args.min_samples)
        chain = options.load_chain(t, args.offline, args.cache_dir)
        trade = options.recommend(outlook, df, chain, args.min_confidence, args.lot_size)
        print(trade.one_liner())
        if trade.action != "NO TRADE":
            counted = [s for s in outlook.signals if s.get("counted")]
            sig = counted[0] if counted else None
            plan = sizing.plan_trade(t, df, outlook.bias, args.horizon, sig["pattern"] if sig else None,
                                     sig["date"] if sig else None, chain, args.capital, args.risk / 100,
                                     lot_override=args.lot_size, offline=args.offline, cache_dir=args.cache_dir)
            print("\n".join(plan.lines()))
            print(f"   (sized for {sizing.money(plan.capital, plan.currency)} capital, {args.risk:g}% risk; "
                  f"lot size from {plan.lot_source})")
        for w in trade.warnings:
            print(f"    ! {w}")
    return 0


def cmd_screen(args) -> int:
    tickers = list(args.tickers or [])
    if args.universe_file:
        tickers += [l.strip() for l in args.universe_file.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    if not tickers:
        tickers = get_universe(args.universe, args.offline)
    picks, ranked, stats = screener.screen(
        tickers, args.period, args.interval, args.horizon, args.lookback, args.top, args.prior_strength,
        args.min_edge, args.news_weight, args.offline, args.cache_dir, not args.no_context,
        size=not args.no_size, capital=args.capital, risk_pct=args.risk / 100,
        affordable_only=args.affordable_only,
    )
    md = screener.render_markdown(picks, ranked, stats, args.horizon, args.top)
    if args.record:
        tracker.record(args.ledger, picks, stats)
    if args.brief:
        name = "custom" if (args.tickers or args.universe_file) else args.universe
        print(screener.render_brief(picks, stats, args.horizon, args.top, name))
    else:
        print(md)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"[screen] written to {args.out}")
    return 0


def cmd_track(args) -> int:
    """What the saved (and replayed) picks would have made, day by day."""
    tickers = list(args.tickers or []) or get_universe(args.universe, args.offline)
    prices = screener.load_universe(tickers, args.period, "1d", args.offline, args.cache_dir, log=lambda *a: None)
    if args.replay:
        n = tracker.replay(tickers, args.replay, prices, args.ledger, args.horizon, args.top, args.capital,
                           args.risk / 100, args.cache_dir)
        if n:
            print(f"[track] replayed {n} past market day(s) from prices (no news)")
    rows = tracker.load_ledger(args.ledger)
    missing = sorted({r["ticker"] for r in rows} - set(prices))
    if missing:
        prices.update(screener.load_universe(missing, args.period, "1d", args.offline, args.cache_dir, log=lambda *a: None))
    ev = tracker.evaluate(rows, prices)
    md = tracker.render_markdown(ev)
    print(tracker.render_brief(ev) if args.brief else md)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
    return 0


def cmd_lab(args) -> int:
    """Search rule variants on older years, judge them on the last few years, and re-check the current strategy."""
    import json as _json

    import pandas as pd

    from . import lab
    from .data import download_prices
    from .tradetest import USER_VALIDATION, load_strategy

    tickers = list(args.tickers or []) or get_universe(args.universe, args.offline)
    prices = screener.load_universe(tickers, "10y", "1d", args.offline, args.cache_dir, log=lambda *a: None)
    sym = index_symbol(args.universe) if not args.tickers else ("^NSEI" if tickers[0].endswith(".NS") else "^GSPC")
    try:
        idx = download_prices(sym, "10y")
    except Exception:
        idx = None
    print(f"[lab] {len(prices)} stocks; testing on the last {args.test_years:g} years ...")
    if args.reverse:
        study = lab.reverse_study(prices, args.test_years, cost=args.cost / 100)
        pd.set_option("display.width", 200)
        print(f"\nReversal study (round-trip cost {args.cost:g}% of premium per option leg):")
        print(lab.summarize_reverse(study).to_string(index=False, float_format=lambda x: f"{x:.3f}"))
        sell = study.loc[study["test"], "sell"]
        print(f"\nSelling, last {args.test_years:g} years: {(sell < -1).mean():.1%} of trades lost more than the whole "
              f"premium received; worst {sell.min():.1f}x the premium.")
        return 0
    if not args.check_only:
        res = lab.run(prices, idx, args.test_years, progress=lambda m, f: print(f"[lab] {m}", end="\r"))
        cols = ["label", "patterns", "train_trades", "train_avg_ret", "test_trades", "test_win_rate", "test_avg_ret",
                "test_profit_factor", "test_worst_streak"]
        pd.set_option("display.width", 220)
        print("\nTop 10 by TRAIN score (their TEST columns are the honest estimate):")
        print(res[cols].head(10).to_string(index=False, float_format=lambda x: f"{x:.3f}"))
        print(f"\nVariants positive on the test years: {(res['test_avg_ret'] > 0).mean():.0%}")
    v = lab.validate(prices, idx, load_strategy(), args.test_years)
    USER_VALIDATION.parent.mkdir(parents=True, exist_ok=True)
    USER_VALIDATION.write_text(_json.dumps(v, indent=1), encoding="utf-8")
    print(f"\nCurrent strategy ({v['strategy']}), {v['test_from']} to {v['test_to']}: {v['test_trades']} trades, "
          f"{v['test_win_rate']:.1%} won, average {v['test_avg_ret']:+.2%} per option trade, "
          f"stock-level {v['test_stock_avg_r']:+.3f}R, worst losing streak {v['test_worst_streak']}.")
    print(screener.validation_line())
    return 0


def cmd_ipo(args) -> int:
    """Recent NSE listings and how they have traded since, plus IPO news."""
    from . import ipo

    df = ipo.listings(args.days, args.offline, args.cache_dir)
    news = [] if args.no_news else ipo.ipo_news(args.offline, args.cache_dir)
    print(ipo.render_text(df, news, args.days))
    return 0


def cmd_market(args) -> int:
    """Crypto (daily) or intraday commodity (15-minute) setups with sizing and a strategy check."""
    from . import markets

    res = markets.scan(args.market, args.capital, args.risk / 100, args.offline, args.cache_dir)
    print(markets.render_text(res))
    return 0


def cmd_goal(args) -> int:
    from . import planner

    g = planner.Goal(name=args.name, target_today=args.target, years=args.years, current=args.current,
                     monthly=args.monthly, step_up=args.step_up / 100, inflation=args.inflation / 100, profile=args.profile,
                     crypto=args.crypto / 100)
    print(planner.render_text(planner.plan(g, args.offline, args.cache_dir)))
    return 0


def cmd_rebalance(args) -> int:
    """Holdings from a CSV with columns name,asset_class,symbol,quantity,value (symbol/quantity or value)."""
    import csv

    from . import rebalance as R
    from .sizing import money

    with open(args.file, newline="", encoding="utf-8") as f:
        rows = [R.Holding(name=r.get("name", ""), asset_class=r.get("asset_class", "other"),
                          value=float(r["value"]) if r.get("value") else None, symbol=r.get("symbol") or None,
                          quantity=float(r["quantity"]) if r.get("quantity") else None) for r in csv.DictReader(f)]
    target = dict(part.split("=") for part in args.target.split(","))
    rec = R.recommend(R.price_holdings(rows, args.offline, args.cache_dir), {k: float(v) for k, v in target.items()},
                      args.band / 100, args.new_money)
    print(rec["verdict"])
    for r in rec["rows"]:
        print(f"  {r['asset_class']:<7} {money(r['value'], '₹'):>14}  now {r['weight']:>4.0%}  target {r['target']:>4.0%}  "
              f"{'buy' if r['trade'] >= 0 else 'sell'} {money(abs(r['trade']), '₹')}")
    for sl in rec["sells"]:
        print(f"  sell {money(sl['amount'], '₹')} of {sl['name']}" + (f" ({sl['quantity']} units)" if sl["quantity"] else ""))
    if rec["new_split"]:
        print("  new money: " + ", ".join(f"{k} {money(v, '₹')}" for k, v in rec["new_split"].items()))
    return 0


def cmd_fund(args) -> int:
    """Rules-based factor portfolio (momentum + low volatility) with an honest backtest."""
    from . import fund
    from .sizing import money

    r = fund.run(fund.FundRules(universe=args.universe, size=args.size, rebalance=args.rebalance), args.capital,
                 args.offline, args.cache_dir)
    m, a = r["metrics"], r["after_tax"]
    c = r["curve"]
    print(f"Model fund: top {args.size} of {r['universe_size']} {args.universe} stocks, rebalanced "
          f"{'monthly' if args.rebalance == 'M' else 'quarterly'} · backtest {c.index[0].date()} to {c.index[-1].date()}")
    for k, label in (("fund", "Model fund"), ("equal_weight", "All stocks, equal weight"), ("nifty", "Nifty 50 ETF")):
        print(f"  {label:<26} {m[k]['cagr']:+.1%} a year · worst fall {m[k]['max_drawdown']:.0%} · ₹1 became ₹{m[k]['growth_of_1']:.2f}")
    print(f"  After tax (you trading it): {a['fund_cagr']:+.1%} a year vs Nifty index fund {a['nifty_cagr']:+.1%}")
    print(f"  Turnover {r['turnover_per_year']:.0%} a year · beat Nifty in {r['beat_nifty_12m']:.0%} and the equal-weight "
          f"basket in {r['beat_equal_12m']:.0%} of 12-month periods")
    print(f"\nToday's portfolio ({r['as_of']}) for {money(args.capital, '₹')}:")
    for h in r["holdings"]:
        print(f"  {h['ticker'].replace('.NS', ''):<12} {h['shares']:>5} shares @ {h['price']:>9.2f} = {money(h['amount'], '₹'):>10}"
              f"  {h['industry']}{'  (new)' if h['new'] else ''}")
    if r["dropped"]:
        print("  Sell (dropped since last rebalance): " + ", ".join(t.replace(".NS", "") for t in r["dropped"]))
    if r["fit_note"]:
        print("  ! " + r["fit_note"])
    print("\nToday's index members are the survivors, which flatters every backtest drawn from them; most of the gap over "
          "the Nifty also shows up in the equal-weight basket. Not investment advice.")
    return 0


def cmd_research(args) -> int:
    horizons = [int(h) for h in args.horizons.split(",")]
    if args.horizon not in horizons:
        horizons.append(args.horizon)
    args.out.mkdir(parents=True, exist_ok=True)
    for t in args.tickers:
        print(f"[research] {t}: loading prices ...")
        df = load_prices(t, args.period, args.interval, args.offline, args.cache_dir)
        print(f"[research] {t}: backtesting {len(df)} bars ...")
        rules = evaluate_rules(df, horizons=horizons, use_context=not args.no_context)
        print(f"[research] {t}: reading news ...")
        heads = load_news(t, args.company, args.offline, args.cache_dir)
        outlook = build_outlook(t, df, rules, heads, horizon=args.horizon, lookback=args.lookback, min_samples=args.min_samples)
        narrative = None
        if args.llm:
            print("[research] asking local model for a briefing ...")
            narrative = llm.narrate("\n".join(outlook.reasons))
            if narrative is None:
                print("[research] Ollama not reachable; skipping narrative")
        md = render_markdown(outlook, rules, heads, args.min_samples, narrative, top_n=args.top)
        if not args.no_options:
            print(f"[research] {t}: loading option chain ...")
            chain = options.load_chain(t, args.offline, args.cache_dir)
            trade = options.recommend(outlook, df, chain, args.min_confidence, args.lot_size)
            md = md.replace("## Best rules", options.render_markdown(trade) + "\n\n## Best rules", 1)
        path = args.out / f"{t.upper()}_{outlook.as_of.date()}.md"
        path.write_text(md, encoding="utf-8")
        print(md)
        print(f"[research] report written to {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows consoles and pipes often default to cp1252, which cannot print ₹, · or —.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="stock_agent", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("research", help="full report: rules + live signals + news + outlook")
    _common(r)
    r.add_argument("--horizon", type=int, default=5, help="holding period used for the outlook")
    r.add_argument("--lookback", type=int, default=3, help="how many recent bars count as a live signal")
    r.add_argument("--company", help="company name for the news search (default: looked up from Yahoo, cached)")
    r.add_argument("--llm", action="store_true", help="add a briefing from a local Ollama model if running")
    r.add_argument("--out", type=Path, default=Path("reports"))
    r.add_argument("--no-options", action="store_true", help="skip the option chain and the call/put verdict")
    r.add_argument("--min-confidence", type=float, default=0.15, help="outlook confidence needed to recommend a trade")
    r.add_argument("--lot-size", type=int, help="shares per option lot (US default 100; NSE lots vary per stock)")
    r.add_argument("--top", type=int, default=10)
    r.set_defaults(func=cmd_research)

    tr = sub.add_parser("trade", help="just the verdict: BUY CALL / BUY PUT / NO TRADE per ticker")
    _common(tr)
    tr.add_argument("--horizon", type=int, default=5)
    tr.add_argument("--lookback", type=int, default=3)
    tr.add_argument("--company")
    tr.add_argument("--min-confidence", type=float, default=0.15)
    tr.add_argument("--lot-size", type=int, help="override the lot size (default: NSE lot file / 100 for US)")
    tr.add_argument("--capital", type=float, help="trading capital (default ₹5,00,000 for NSE, $25,000 for US)")
    tr.add_argument("--risk", type=float, default=2.0, help="percent of capital to risk per trade (default 2)")
    tr.set_defaults(func=cmd_trade)

    sc = sub.add_parser("screen", help="scan a universe and pick the top N setups by success rate + news")
    sc.add_argument("tickers", nargs="*", help="symbols to scan (default: the --universe list)")
    sc.add_argument("--universe", choices=CHOICES, default="nifty50")
    sc.add_argument("--universe-file", type=Path, help="text file with one symbol per line")
    sc.add_argument("--period", default="10y")
    sc.add_argument("--interval", default="1d")
    sc.add_argument("--horizon", type=int, default=5, help="holding period in bars (default 5)")
    sc.add_argument("--lookback", type=int, default=3, help="a setup counts if it completed in the last N bars")
    sc.add_argument("--top", type=int, default=10)
    sc.add_argument("--prior-strength", type=int, default=20, help="pseudo-signals pulling small samples to the universe rate")
    sc.add_argument("--min-edge", type=float, default=0.02, help="minimum success rate above baseline")
    sc.add_argument("--news-weight", type=float, default=0.10, help="max score shift from news sentiment")
    sc.add_argument("--offline", action="store_true")
    sc.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    sc.add_argument("--no-context", action="store_true")
    sc.add_argument("--out", type=Path, help="also write the table to this markdown file")
    sc.add_argument("--brief", action="store_true", help="short plain-text output for notifications")
    sc.add_argument("--capital", type=float, help="trading capital (default ₹5,00,000 for NSE, $25,000 for US)")
    sc.add_argument("--risk", type=float, default=2.0, help="percent of capital to risk per trade (default 2)")
    sc.add_argument("--no-size", action="store_true", help="skip lots / stop-loss / target sizing")
    sc.add_argument("--affordable-only", action="store_true", help="only list picks where at least 1 lot fits your risk")
    sc.add_argument("--record", action="store_true", help="save these picks to the track-record ledger")
    sc.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER, help="track-record file (default ~/.stock_agent/track.json)")
    sc.set_defaults(func=cmd_screen)

    ap_ = sub.add_parser("app", help="open the point-and-click app in your browser")
    ap_.add_argument("--port", type=int, default=8765)
    ap_.add_argument("--no-browser", action="store_true", help="start the server without opening a browser")
    ap_.add_argument("--phone", action="store_true", help="also open it to phones on your Wi-Fi (with an access key)")
    ap_.add_argument("--tunnel", action="store_true", help="a secure https link for your phone on any network (needs cloudflared)")
    ap_.add_argument("--cloudflared", help="path to cloudflared if it is not found automatically")
    ap_.set_defaults(func=lambda a: (__import__("stock_agent.app", fromlist=["serve"]).serve(
        a.port, not a.no_browser, a.phone, a.tunnel, a.cloudflared), 0)[1])

    pb = sub.add_parser("publish", help="build the phone app (static site + tonight's results) into a folder")
    pb.add_argument("--out", default="_site")
    pb.add_argument("--only", nargs="*", help="publish only these parts: screen track crypto commodities fund ipo prices")
    pb.set_defaults(func=lambda a: __import__("stock_agent.publish", fromlist=["main"]).main(a.out, a.only))

    sh = sub.add_parser("shortcut", help="put a 'Stock Agent' launcher on your desktop")
    sh.add_argument("--phone", action="store_true", help="a launcher that also serves your phone")
    sh.add_argument("--tunnel", action="store_true", help="a launcher with a secure link for any network")
    sh.set_defaults(func=lambda a: (print(f"Created {__import__('stock_agent.app', fromlist=['make_shortcut']).make_shortcut(a.phone, a.tunnel)}"
                                          " - double-click it to open the app."), 0)[1])

    for mk, helptext in (("crypto", "Bitcoin and other coins: setups, sizing in coins, strategy check"),
                         ("commodities", "intraday gold, silver, crude, gas, copper on 15-minute candles")):
        mp = sub.add_parser(mk, help=helptext)
        mp.add_argument("--capital", type=float, default=200000)
        mp.add_argument("--risk", type=float, default=2.0)
        mp.add_argument("--offline", action="store_true")
        mp.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
        mp.set_defaults(func=cmd_market, market=mk)

    fp = sub.add_parser("fund", help="model factor fund: today's 25 stocks and an honest backtest")
    fp.add_argument("--universe", choices=["nifty200", "nifty100", "fno"], default="nifty200")
    fp.add_argument("--size", type=int, default=25)
    fp.add_argument("--rebalance", choices=["M", "Q"], default="Q")
    fp.add_argument("--capital", type=float, default=200000)
    fp.add_argument("--offline", action="store_true")
    fp.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    fp.set_defaults(func=cmd_fund)

    gp = sub.add_parser("goal", help="long-term goal planner: monthly investment, mix and chance of success")
    gp.add_argument("--name", default="My goal")
    gp.add_argument("--target", type=float, required=True, help="amount needed, in today's rupees")
    gp.add_argument("--years", type=float, required=True)
    gp.add_argument("--current", type=float, default=0)
    gp.add_argument("--monthly", type=float, default=10000)
    gp.add_argument("--step-up", type=float, default=5, help="yearly increase of the monthly amount, %%")
    gp.add_argument("--inflation", type=float, default=6)
    gp.add_argument("--profile", choices=["conservative", "balanced", "aggressive"], default="balanced")
    gp.add_argument("--crypto", type=float, default=0, help="share in crypto, %% of the plan (0-10, goals of 5+ years)")
    gp.add_argument("--offline", action="store_true")
    gp.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    gp.set_defaults(func=cmd_goal)

    rp = sub.add_parser("rebalance", help="compare holdings with a target mix and list the trades")
    rp.add_argument("file", help="CSV with name,asset_class,symbol,quantity,value")
    rp.add_argument("--target", default="equity=60,debt=30,gold=10", help="e.g. equity=60,debt=30,gold=10")
    rp.add_argument("--band", type=float, default=5)
    rp.add_argument("--new-money", type=float, default=0)
    rp.add_argument("--offline", action="store_true")
    rp.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    rp.set_defaults(func=cmd_rebalance)

    ip = sub.add_parser("ipo", help="new NSE listings since their IPO, and IPO news")
    ip.add_argument("--days", type=int, default=90, help="listed within this many days (default 90)")
    ip.add_argument("--no-news", action="store_true")
    ip.add_argument("--offline", action="store_true")
    ip.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    ip.set_defaults(func=cmd_ipo)

    lb = sub.add_parser("lab", help="test rule variants on years they were not tuned on")
    lb.add_argument("tickers", nargs="*")
    lb.add_argument("--universe", choices=CHOICES, default="nifty50")
    lb.add_argument("--test-years", type=float, default=3.0)
    lb.add_argument("--check-only", action="store_true", help="only re-check the current strategy (fast)")
    lb.add_argument("--reverse", action="store_true", help="compare buying with flipping, selling and credit spreads")
    lb.add_argument("--cost", type=float, default=2.0, help="round-trip cost per option leg, %% of premium (default 2)")
    lb.add_argument("--offline", action="store_true")
    lb.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    lb.set_defaults(func=cmd_lab)

    tk = sub.add_parser("track", help="what-if P&L of saved picks, day by day")
    tk.add_argument("tickers", nargs="*", help="universe to replay (default: --universe)")
    tk.add_argument("--universe", choices=CHOICES, default="nifty50")
    tk.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    tk.add_argument("--replay", type=int, default=0, help="rebuild picks for the last N market days missing from the ledger")
    tk.add_argument("--period", default="10y")
    tk.add_argument("--horizon", type=int, default=5)
    tk.add_argument("--top", type=int, default=10)
    tk.add_argument("--capital", type=float)
    tk.add_argument("--risk", type=float, default=2.0)
    tk.add_argument("--offline", action="store_true")
    tk.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    tk.add_argument("--brief", action="store_true", help="short text for notifications")
    tk.add_argument("--out", type=Path, help="also write the full report to this markdown file")
    tk.set_defaults(func=cmd_track)

    ru = sub.add_parser("rules", help="rank candlestick rules by historical win rate")
    _common(ru)
    ru.add_argument("--top", type=int, default=15)
    ru.add_argument("--pooled", action="store_true", help="merge all tickers into one table to find rules that generalise")
    ru.set_defaults(func=cmd_rules)

    n = sub.add_parser("news", help="headlines with sentiment")
    n.add_argument("tickers", nargs="+")
    n.add_argument("--company")
    n.add_argument("--offline", action="store_true")
    n.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    n.add_argument("--top", type=int, default=20)
    n.set_defaults(func=cmd_news)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
