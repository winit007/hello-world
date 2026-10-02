# stock_agent — offline stock research agent

A local agent that pulls a stock's price history and headlines, reads every classic candlestick
pattern, **backtests each pattern on that stock's own history**, and tells you which rule
("pattern X completed → hold N days") has had the best win rate (conversion ratio). It then combines
the patterns firing right now, the trend and the news sentiment into a short-term outlook and turns
that into an options verdict: **BUY CALL, BUY PUT or NO TRADE**, with the contract and its historical odds.

Everything runs on your machine. The only network calls are the free Yahoo Finance price feed and
two public RSS feeds, and both are cached so you can re-run with `--offline` on a plane. The optional
narrative step uses a local model through [Ollama](https://ollama.com); nothing is sent to a cloud LLM.

## Install

As an app (needs Python 3.10+; Git is not required):

```bash
pip install https://github.com/winit007/hello-world/archive/refs/heads/ccr-d382c6bc-vzy6y0.zip
python -m stock_agent screen --brief --capital 200000 --risk 2
```

The install also creates a `stock-agent` command, but on Windows with Python from the Microsoft Store
its folder is not on PATH, so `python -m stock_agent` is the reliable way to run it. To update later,
run the same `pip install` line with `--force-reinstall --no-deps` added. Reports and caches are written
to the folder you run it from (`reports\` and `.cache\`).

Or from a clone: `pip install -r requirements.txt` and use `python -m stock_agent ...`.
Every `python -m stock_agent` example below also works as `stock-agent` when its folder is on PATH.

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

# one line per ticker: BUY CALL / BUY PUT / NO TRADE with strike, expiry, premium and odds
python -m stock_agent trade AAPL TSLA NVDA MSFT --period 10y

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

## Top 5 picks: the screener

```bash
python -m stock_agent screen                          # Nifty 50, top 5, 5-day holding period
python -m stock_agent screen --universe us            # US mega caps
python -m stock_agent screen --horizon 10 --top 10    # longer hold, more picks
python -m stock_agent screen INFY.NS TCS.NS WIPRO.NS HCLTECH.NS TECHM.NS   # your own list
python -m stock_agent screen --universe-file mylist.txt --out reports/screen.md
python -m stock_agent screen --brief                  # 5 short lines, for a phone notification
```

For every stock in the universe the screener:

1. **Finds active setups**: candlestick patterns that completed in the last `--lookback` bars (default 3).
2. **Measures the success rate** of that pattern on that stock over the holding period, blended with
   the same pattern's rate across the whole universe: `(wins + 20 × universe rate) / (signals + 20)`.
   A stock with 120 past signals keeps its own rate; one with 8 mostly inherits the universe's. This
   stops a lucky 5-for-5 history from topping the list.
3. **Filters** out setups less than 2 points above the stock's baseline, setups whose average move is
   not in the trade's direction, and stocks where bullish and bearish setups with an edge fire together.
4. **Reads the news** for the survivors and shifts the score by up to ±0.10: headlines agreeing with
   the trade push it up, disagreeing ones push it down, and strongly contradicting news removes it.
5. **Checks recency**: the *Last 3y* column repeats the test on recent data only and flags a pick
   whose edge has faded.
6. **Ranks** by score and prints the top N as BUY CALL / BUY PUT ideas. See
   [docs/sample_screen_nifty50.md](../docs/sample_screen_nifty50.md).

If fewer than N stocks qualify, fewer are shown. On quiet days the honest answer can be zero picks.

### Exact orders: lots, stop-loss, targets

Each pick is turned into an order sized to your account (`--capital`, default ₹5,00,000 for NSE or
$25,000 for US, and `--risk`, default 2% per trade):

```
1. HINDALCO BUY CALL @ 955.20 · Doji after downtrend · 70% success (base 55%, 3y 81%) · news +0.22
   BUY 1 lot HINDALCO 27-Oct-2026 960 CE · lot 700 · premium ~₹28.05 · cost ₹19,635
   STOP: sell if HINDALCO closes below 939.42 (premium ~₹20.05)
   TARGET: book half at 978.87 (~₹41.15), rest at 994.66 (~₹52.00) · time exit 06-Oct
```

- **Lot size** comes from NSE's `fo_mktlots.csv` for the contract month (refreshed daily, with a bundled
  September 2026 snapshot as fallback). US contracts are 100 shares.
- **Contract**: at-the-money strike, first monthly expiry that outlives the holding period (NSE: last
  Tuesday of the month). The live option chain is used when reachable.
- **Stop-loss** on the stock: just beyond the pattern's low (CALL) or high (PUT), padded by a quarter
  of the 14-day ATR, kept between 0.75 and 2.5 ATR from entry. The matching option premium is shown
  so you can place the exit on the option itself.
- **Targets**: 1.5× and 2.5× the stop distance; book half at the first. **Time exit** after the
  holding period if neither level is hit.
- **Lots** = risk budget ÷ loss per lot at the stop, capped so premium stays under 25% of capital.
  "0 lots" means a single lot already risks more than your budget.
- Without a live chain the premium is a Black-Scholes estimate from recent volatility. Check the live
  quote before ordering; the stop and target levels on the stock stay valid either way.

```bash
stock-agent screen --capital 300000 --risk 1.5 --brief
stock-agent trade HINDALCO.NS --capital 300000
```

## The call / put verdict

`trade` (and the *Options trade* section of `research`) works like this:

1. **Direction** comes from the outlook. Neutral bias, or confidence under `--min-confidence`
   (default 15%), is NO TRADE: buying premium without an edge is a coin flip minus time decay.
2. **Expiry** is the first listed expiry that still has time value after the holding period ends
   (`--horizon` bars ≈ calendar days × 7/5, plus a 3-day buffer).
3. **Strike** is the at-the-money contract with a live price. Premium is the bid/ask mid, or the
   last trade when the market is closed (a warning says so).
4. **Reality check.** Breakeven = strike ± premium. The agent looks at every time the same
   candlestick setup fired on this stock (or every bar, when no counted pattern is active) and
   measures how often price got past the breakeven within the holding period, and the average
   P&L per 100-share contract at that point with leftover time value counted as zero. If that
   average is negative the verdict is downgraded to NO TRADE even when the direction looks right,
   because the premium is eating the expected move.
5. **Volatility.** Implied vol is read from the chain, or solved from the price with Black-Scholes
   when the feed returns zeros after hours, and compared with 20-day realised vol. A rich premium
   triggers a suggestion to use a debit spread instead.

Example output:

```
AAPL: BUY CALL 337.5 exp 2026-10-09 @ 6.25 (spot 338.40, breakeven 343.75, needs +1.6%) · hist. P(profit) 40% · avg P&L/contract +34
MSFT: NO TRADE — bullish bias, but the ATM call needs +2.0% in 5 bars; history cleared that only 30% of the time ... average -205 per contract
TSLA: NO TRADE — bias neutral with 1% confidence is below the 15% threshold
```

Option chains come from Yahoo and are cached under `.cache/options/` for offline runs.

**Indian stocks (NSE/BSE).** Use Yahoo symbols such as `RELIANCE.NS`, `TCS.NS`, `INFY.BO`. Prices and
news work the same way and the company name is looked up automatically for the news search. Yahoo
carries no option chains for NSE, so the agent fetches the chain from NSE's own website
(`nseindia.com/api/option-chain-equities`). NSE requires a browser-style cookie handshake and blocks
some networks (cloud servers, VPNs); when that happens the verdict is direction-only and a
`[options] chain fetch failed` line explains why. NSE lot sizes vary by stock and are not in the
chain, so pass `--lot-size` (for example `--lot-size 500` for RELIANCE) to get P&L per lot instead
of per share.

```bash
python -m stock_agent trade RELIANCE.NS TCS.NS --period 10y --lot-size 500
python -m stock_agent research RELIANCE.NS TCS.NS INFY.NS --period 10y
```

## Layout

```
stock_agent/
  data.py       price download + CSV cache (offline mode)
  patterns.py   22 candlestick detectors in plain pandas
  backtest.py   forward-return evaluation, Wilson ranking, cross-ticker pooling
  news.py       Google News + Yahoo RSS, filing-spam filter, VADER sentiment, themes
  report.py     outlook scoring and markdown report
  options.py    call/put verdict: expiry & strike selection, breakeven odds, Black-Scholes IV
  screener.py   top-N picks across a universe: shrunk success rate + news tilt + recency check
  universes.py  built-in Nifty 50 and US mega-cap lists
  sizing.py     exact order: contract, lots for your capital/risk, stop-loss, targets, time exit
  lots.py       NSE lot sizes (live fo_mktlots.csv, cached daily, bundled snapshot fallback)
  llm.py        optional local Ollama narrative
  __main__.py   CLI (screen / research / trade / rules / news)
tests/          unit tests: python -m unittest discover -s tests
```

## Honest caveats

- Past win rates are descriptive statistics, not a forecast. Most single-ticker rules have small
  samples; prefer the `--pooled` view and rules whose Wilson LB stays above 50%.
- No transaction costs, slippage or overnight gaps are modelled. Returns are close-to-close.
- News sentiment is headline-only and lexicon-based; it is a tilt, not an analysis.
- Options P&L is estimated at the end of the holding period with remaining time value set to zero,
  and ignores commissions and the bid/ask spread. Long options can and regularly do go to zero.
- Nothing here is investment advice.
