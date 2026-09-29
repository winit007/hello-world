# stock_agent — offline stock research agent

A local agent that pulls a stock's price history and headlines, reads every classic candlestick
pattern, **backtests each pattern on that stock's own history**, and tells you which rule
("pattern X completed → hold N days") has had the best win rate (conversion ratio). It then combines
the patterns firing right now, the trend and the news sentiment into a short-term outlook.

Everything runs on your machine. The only network calls are the free Yahoo Finance price feed and
two public RSS feeds, and both are cached so you can re-run with `--offline` on a plane. The optional
narrative step uses a local model through [Ollama](https://ollama.com); nothing is sent to a cloud LLM.

## Install

```bash
pip install -r requirements.txt
```

## Use

```bash
# full report: ranked rules, live signals, news sentiment, outlook → reports/AAPL_<date>.md
python -m stock_agent research AAPL --period 10y --company Apple

# several tickers at once (NSE/BSE symbols work too: RELIANCE.NS, TCS.BO)
python -m stock_agent research AAPL MSFT RELIANCE.NS

# just the candlestick rule table for one stock
python -m stock_agent rules AAPL --period 10y --top 15

# find rules that work ACROSS a watchlist (much more trustworthy than one ticker)
python -m stock_agent rules AAPL MSFT NVDA JPM XOM --period 10y --pooled

# headlines with sentiment and themes
python -m stock_agent news TSLA --company Tesla

# no network: reuse the cache written by earlier runs
python -m stock_agent research AAPL --offline

# add a 5-sentence briefing from a local Ollama model (OLLAMA_MODEL=llama3.1 by default)
python -m stock_agent research AAPL --llm
```

Useful flags: `--horizons 1,3,5,10` (holding periods to test), `--horizon 5` (the one used for the
outlook), `--min-samples 10` (occurrences a rule needs before it is ranked), `--lookback 3` (how many
recent bars count as a live signal), `--interval 1wk` (weekly candles), `--no-context` (ignore the
prior-trend requirement of reversal patterns).

## How the numbers are produced

| Column | Meaning |
|---|---|
| **Win rate** | Share of signals followed by a move in the pattern's direction after the holding period. This is the "conversion ratio". |
| **Baseline** | The stock's unconditional chance of moving that way over the same horizon. A 60% win rate means nothing on a stock that rises 60% of all weeks anyway. |
| **Edge** | Win rate minus baseline. |
| **Avg return** | Mean directional return per signal (bearish patterns are scored on the short side). |
| **Profit factor** | Sum of winning returns divided by sum of losing returns. |
| **Wilson LB** | Lower bound of the 95% confidence interval of the win rate. Ranking uses this so a 3-for-3 pattern never outranks a 62% pattern seen 200 times. |

Patterns (22): Hammer, Inverted Hammer, Hanging Man, Shooting Star, Doji (after up/down trend),
Dragonfly Doji, Gravestone Doji, Bullish/Bearish Marubozu, Bullish/Bearish Engulfing, Bullish/Bearish
Harami, Piercing Line, Dark Cloud Cover, Tweezer Top/Bottom, Morning Star, Evening Star, Three White
Soldiers, Three Black Crows. Reversal patterns only count when the prior bar was on the correct side
of the 20-bar moving average, as the textbook definition requires.

The outlook score is 60% technical (active patterns weighted by their proven edge), 25% news
sentiment (VADER with a finance-tuned lexicon) and 15% trend (close vs SMA20 vs SMA50). Patterns
with no historical edge on that ticker are shown but not counted.

## Layout

```
stock_agent/
  data.py       price download + CSV cache (offline mode)
  patterns.py   22 candlestick detectors in plain pandas
  backtest.py   forward-return evaluation, Wilson ranking, cross-ticker pooling
  news.py       Google News + Yahoo RSS, filing-spam filter, VADER sentiment, themes
  report.py     outlook scoring and markdown report
  llm.py        optional local Ollama narrative
  __main__.py   CLI (research / rules / news)
tests/          unit tests: python -m unittest discover -s tests
```

## Honest caveats

- Past win rates are descriptive statistics, not a forecast. Most single-ticker rules have small
  samples; prefer the `--pooled` view and rules whose Wilson LB stays above 50%.
- No transaction costs, slippage or overnight gaps are modelled. Returns are close-to-close.
- News sentiment is headline-only and lexicon-based; it is a tilt, not an analysis.
- Nothing here is investment advice.
