"""Command line entry point.

    python -m stock_agent research AAPL MSFT        # full report per ticker
    python -m stock_agent rules AAPL --period 10y   # only the ranked candlestick rules
    python -m stock_agent news AAPL                 # only headlines + sentiment
    python -m stock_agent trade AAPL TSLA NVDA      # one line each: BUY CALL / BUY PUT / NO TRADE
    python -m stock_agent research AAPL --offline   # use cached prices/news, no network
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import llm, options
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
        trade = options.recommend(outlook, df, chain, args.min_confidence)
        print(trade.one_liner())
        for w in trade.warnings:
            print(f"    ! {w}")
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
            trade = options.recommend(outlook, df, chain, args.min_confidence)
            md = md.replace("## Best rules", options.render_markdown(trade) + "\n\n## Best rules", 1)
        path = args.out / f"{t.upper()}_{outlook.as_of.date()}.md"
        path.write_text(md)
        print(md)
        print(f"[research] report written to {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stock_agent", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("research", help="full report: rules + live signals + news + outlook")
    _common(r)
    r.add_argument("--horizon", type=int, default=5, help="holding period used for the outlook")
    r.add_argument("--lookback", type=int, default=3, help="how many recent bars count as a live signal")
    r.add_argument("--company", help="company name for the news search (default: ticker)")
    r.add_argument("--llm", action="store_true", help="add a briefing from a local Ollama model if running")
    r.add_argument("--out", type=Path, default=Path("reports"))
    r.add_argument("--no-options", action="store_true", help="skip the option chain and the call/put verdict")
    r.add_argument("--min-confidence", type=float, default=0.15, help="outlook confidence needed to recommend a trade")
    r.add_argument("--top", type=int, default=10)
    r.set_defaults(func=cmd_research)

    tr = sub.add_parser("trade", help="just the verdict: BUY CALL / BUY PUT / NO TRADE per ticker")
    _common(tr)
    tr.add_argument("--horizon", type=int, default=5)
    tr.add_argument("--lookback", type=int, default=3)
    tr.add_argument("--company")
    tr.add_argument("--min-confidence", type=float, default=0.15)
    tr.set_defaults(func=cmd_trade)

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
