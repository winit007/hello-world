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

## The app (easiest)

```bat
python -m stock_agent app        :: opens the app in your browser
python -m stock_agent shortcut   :: puts a "Stock Agent" icon on your desktop; double-click it next time
```

The app runs on your own computer and opens at `http://127.0.0.1:8765`. Keep the black window
that appears open while you use it; close it to stop the app. It has five pages:

| Page | What it does |
|---|---|
| **Today's picks** | Press *Scan now* for the top 5 setups as cards: success rate vs the usual rate, the exact order (contract, lots, cost), stop-loss, two targets and the exit date. Tick *Only trades I can afford* to hide picks that break your risk limit. |
| **Stock lookup** | Type any symbol (TCS.NS, AAPL) for its verdict, a 6-month candlestick chart with patterns and your stop/targets marked, its best candlestick rules and its latest news. |
| **Track record** | What every pick would have made if you had taken it: profit or loss for each day, a running total, and the outcome of each pick (target, stop, time exit). *Rebuild last 10 days* fills in history straight away. |
| **Paper trading** | A practice account with virtual money (your capital from Settings; reset any time). Buy or short NSE shares, buy NSE stock options, trade crypto and MCX commodities at live prices with real lot sizes, slippage and charges. Stop-loss and target close positions automatically, intraday trades square off at 3:20 pm, and it tracks your win rate, P&L, charges and worst drop. *Paper trade* on any card fills in the order. |
| **My trades** | *Add to my trades* from any pick, then enter the price you sold at to close it. Shows your win rate and total profit or loss. |
| **Settings** | Capital, risk per trade, which stocks to scan (Nifty 50, US, or your own list), holding period. |
| **How to use** | The three-step routine: scan in the evening, buy in the morning, manage the exit. |

Settings, your trade journal and the downloaded data are kept in a `.stock_agent` folder in your
user folder, so nothing is lost when you update the app. The page works in light and dark mode and on
a phone-sized window.

## On your Android phone

**Easiest, works on Wi-Fi and mobile data (no firewall or router changes):**

```bat
winget install --id Cloudflare.cloudflared      :: once
python -m stock_agent shortcut --tunnel          :: once: puts "Stock Agent (anywhere)" on the desktop
```

Double-click **Stock Agent (anywhere)**. It starts the app and a free Cloudflare quick tunnel, which makes
an outgoing connection, so Windows Firewall, VPNs and router settings do not get in the way. The window
prints an `https://….trycloudflare.com/?key=…` link; open *Settings* on the PC and scan its QR code with
the phone's camera. Requests through the tunnel must present a 16-character key (then kept in a secure
cookie), wrong keys are slowed down, and the app still listens only to this computer. The link changes
each time you start it, so scan the new QR code then. Anyone with the full link could open your app:
do not share it, and do not leave it running when you are not using it, especially with Kite connected.

**Same Wi-Fi only:**

```bat
python -m stock_agent app --phone          :: or once: python -m stock_agent shortcut --phone
```

The window prints a link such as `http://192.168.1.20:8765/?key=AbC123xy`. On your phone, connected to
the same Wi-Fi, open it in Chrome once (the phone keeps the key in a cookie), then tap **⋮ > Add to Home
screen** for a Stock Agent icon that opens full-screen like an app. Every page is laid out for phone
screens. The computer must be on with the app running; if Windows asks about the firewall, allow Python on
*Private networks*. Without `--phone` the app only listens to this computer. In phone mode other devices
need the key, requests must address the computer by its IP (which blocks DNS-rebinding tricks), and
every action still needs the page's own token. The key is kept in `.stock_agent/phone_key.txt`; delete
that file to issue a new one.

The daily top-5 message already reaches the phone as a push notification and email without the computer.

## Use from the command line

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
   STOP: sell if HINDALCO trades below 939.42 (premium ~₹20.05)
   TARGET: book half at 978.87 (~₹41.15), rest at 994.66 (~₹52.00) · time exit 06-Oct
```

- **Lot size** comes from NSE's `fo_mktlots.csv` for the contract month (refreshed daily, with a bundled
  September 2026 snapshot as fallback). US contracts are 100 shares.
- **Contract**: at-the-money strike, first monthly expiry that outlives the holding period (NSE: last
  Tuesday of the month). The live option chain is used when reachable.
- **Stop-loss** on the stock: just beyond the pattern's low (CALL) or high (PUT), padded by a quarter
  of the 14-day ATR, kept between 0.75 and 2.5 ATR from entry. It is an intraday stop: sell as soon as
  the stock trades through it (a Kite GTT on the option premium does exactly this), not at the close. The matching option premium is shown
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

## Paper trading: practise with virtual money

The **Paper trading** tab is a simulated broker account stored in `~/.stock_agent/paper.json`.

* **What you can trade:** NSE shares (delivery, or intraday with 20% margin and shorting), NSE stock options (buy only, priced with Black-Scholes at the volatility fixed when you buy, settled at intrinsic value on expiry), crypto (US-dollar prices converted at the live USD/INR rate) and MCX mini commodity contracts (10% margin, long or short).
* **Fills:** market orders at the latest Yahoo price plus slippage (0.05% shares, 1% options, 0.1% crypto, 0.02% commodities), and only during market hours: NSE 9:15 am to 3:30 pm, MCX 9 am to 11:30 pm, crypto any time. NSE prices are about 15 minutes delayed.
* **Charges:** brokerage, STT/CTT, exchange fees, SEBI fee, GST and stamp duty at Indian rates, simplified.
* **Automatic exits:** each refresh (every minute while the tab is open) walks the 5-minute candles since entry. The first level touched closes the position; when stop and target fall in the same candle, the stop wins. Intraday positions square off at 3:20 pm (MCX 11:15 pm). Positions with an *Exit by* date close after it.
* **Results:** account value, free cash, total P&L, win rate with average win and loss, charges paid, worst drop from the peak, an account-value chart, closed trades and an activity log.

Option premiums are model prices, not live NSE quotes, so real fills will differ, most of all for far out-of-the-money strikes.

## Track record: what-if profit and loss

```bash
python -m stock_agent screen --record                 # save today's picks (the app does this on every scan)
python -m stock_agent track --brief                   # what they made since, day by day
python -m stock_agent track --replay 10 --brief       # also rebuild the last 10 market days from prices
```

Each saved pick is scored with the plan's own rules on the real daily candles that followed:

1. **Buy** at the next trading day's open. If the stock already opened beyond the stop, the pick is
   **skipped** (the setup is broken), exactly as the Kite dialog would refuse it.
2. **Stop**: as soon as the stock trades through the stop, everything is sold there (or at the open after
   a gap). If the stop and a target are touched on the same day, the stop is assumed first.
3. **Targets**: half at Target 1 and the rest at Target 2 (a single lot exits fully at Target 1).
4. **Time exit**: anything left is sold at the close of the exit date.

Option prices come from Black-Scholes using the volatility implied by the pick's own premium, because
no free history of NSE option prices exists. Brokerage, taxes and the bid/ask spread are not deducted.
*Your account* counts picks that fitted your risk limit at their real lot count; *all picks* counts
every pick at one lot. Replayed days are rebuilt without news, so they can differ slightly from what the
daily message sent on those days.

## How good is it? The strategy lab

```bash
python -m stock_agent lab                # test 72 rule variants (about 3 minutes)
python -m stock_agent lab --check-only   # only re-check the current rules (seconds)
```

Every past signal is replayed as the exact trade the agent recommends: buy the at-the-money option at
the next open, intraday stop, Target 1, time exit, option values from Black-Scholes. The lab varies the
stop width, target, holding period, a 200-day-trend filter and a Nifty-trend filter. Each variant picks
its patterns on the older years only and is then judged on the last 3 years it never saw.

**Result on the Nifty 50, October 2023 to October 2026 (13,063 trades):** 38% of trades made money and
the average option trade lost 2.2%. At the stock level the same trades averaged about zero. In other words,
candlestick patterns on these stocks behaved like coin flips over this period, and buying options turned
that into a steady loss through time decay. Only 19% of the 72 variants were positive on the test years,
about what luck alone produces, and variants that looked best on the older years were negative on the
newer ones. No filter improved results consistently, so the rules were left unchanged.

**Reversing it does not help either** (`python -m stock_agent lab --reverse`). On the same signals and
exits over the last 3 years, with 2% of premium per option leg for brokerage, taxes and the bid/ask spread:

| Reversal | Trades won | Average per trade | Worst trade |
|---|---|---|---|
| Buy the option (current) | 37% | -4.2% of premium | -100% |
| Flip: buy the opposite option | 38% | -2.9% of premium | -100% |
| Sell the recommended option | 61% | +0.2% of premium | -1,151% (11.5x the premium) |
| Credit spread (capped loss) | 53% | -4.0% of the capped risk | -105% |

Flipping fails because the stock moves were already coin flips; the loss came from time decay, which a
buyer pays in both directions. Selling wins most often because the seller collects that decay, but the
average win (+33% of premium) is smaller than the average loss (-51%), 1 trade in 26 loses more than the
whole premium, and a one-at-a-time sample of these trades fell ₹2.1 lakh below its peak at ₹20,000 of
premium per trade, more than a ₹2 lakh account, which also could not post the roughly ₹1-1.5 lakh
exchange margin a single naked stock-option lot needs. Even the small positive average leans on the
model's assumption that options are priced 10% above recent volatility. The app therefore keeps buying
(defined risk, practice mode) and does not recommend selling options.

There is no rule that never loses. Strategies that win almost every time (for example selling options)
do so by taking rare, very large losses. The app and the daily message therefore show this check on
every scan, and the Kite dialog warns while it is negative: **use the picks for research and practice
mode, not real money, unless a future lab run shows a positive result on untouched data.**

The *success rate* on each pick is this trade-based win rate (blended with the universe rate for small
samples), compared with the same trade started on an ordinary day.

## Which stocks it scans

Pick the list in *Settings* (app) or with `--universe` (command line):

| List | Stocks | Notes |
|---|---|---|
| `nifty50` | 50 | default |
| `nifty100` | 100 | |
| `fno` | about 215 | every NSE stock with options, so every pick can be an option order |
| `nse_all` | about 2,300 | every NSE stock in the main segment; first scan downloads 10 years for all of them (about 7 minutes), later scans only fetch the newest days |
| `sp500` | about 500 | US |
| `us` | 30 | US mega caps |

The lists come from NSE's own files (and a public S&P 500 file), refreshed once a day, with dated copies
bundled for offline use. Prices are downloaded in batches and cached; only the last few months are
fetched for stocks already in the cache, and a full history is reloaded when a split or bonus has
re-adjusted it. Stocks trading less than about ₹2 crore a day ($5 million in the US) are skipped as
thinly traded. Picks on stocks without options get a share order instead (shares to buy, cost, stop,
targets); bearish picks without options are marked as not tradable, since delivery shares cannot be shorted.

```bash
python -m stock_agent screen --universe fno --brief --capital 200000 --risk 2
python -m stock_agent screen --universe nse_all --brief
```

## The news behind each pick

Each pick lists what the last 14 days of headlines say about that trade: how many headlines, how many
support or go against it, the main themes, and the strongest ones with their date, source, score and a
link. News only moves a pick's score by up to ±0.10 (and removes picks it strongly contradicts); the
candlestick backtest chooses the candidates.

## New IPOs

```bash
python -m stock_agent ipo --days 90
```

The app's *New IPOs* page lists stocks first listed on NSE in the last 30, 90 or 365 days with their
listing price (first day's open), day-one move, latest price, change since listing and distance from
their high, plus the latest IPO news (upcoming issues, subscriptions, allotments, listings). The list comes
from NSE's equity file, so it also includes a few demergers and relistings. New listings have too little
history for the candlestick backtest, so they never appear in the picks and their stock page shows the
chart and news without a verdict.

## Crypto and intraday commodities

```bash
python -m stock_agent crypto --capital 200000 --risk 2
python -m stock_agent commodities --capital 200000 --risk 2
```

The *Crypto* and *Commodities* tabs run the same candlestick trade engine on 14 large coins (daily
candles, 10 years, buy-only) and on gold, silver, crude oil, Brent, natural gas, copper and platinum
(15-minute candles for the last 60 days, long or short, exit within 2 hours). Each card shows the setup,
how often it won as a plain trade after costs (0.5% for crypto, 0.05% for commodities) against random
entries, and a sized plan: coins for crypto, MCX mini-contract lots for commodities, with stop and
targets. Each tab opens with its own out-of-sample strategy check. On the data available in October 2026
neither showed an edge (crypto 44% won, -0.43% per trade; commodities 40% won, -0.05% per trade).
Commodity prices are the US futures; MCX follows them in rupees with import duty, so levels are approximate.

## Model fund: a stock portfolio built like a factor fund

```bash
python -m stock_agent fund --universe nifty200 --size 25 --rebalance Q --capital 200000
```

The *Model fund* tab works the way systematic fund houses run factor funds. On each rebalance date
(quarter or month end) it ranks the liquid Nifty 200 stocks by risk-adjusted 12-1 month momentum (the
return from 12 months ago to 1 month ago, divided by volatility) plus half a weight of low volatility,
holds the top 25 in equal weight with at most 5 from one industry, and lists today's portfolio with
the number of shares for your capital (stocks whose single share costs more than an equal slot are
left out and the money is spread over the rest). The rules are textbook and fixed in advance.

The backtest uses dividend-adjusted prices, 0.25% cost on every rupee traded, and an after-tax account
(20% on gains held under a year, 12.5% after, losses carried forward) compared with a Nifty index fund
taxed once at the end. Two benchmarks: the Nifty 50 ETF and an equal-weight basket of the whole universe.

Result, end of 2017 to October 2026, quarterly: 24.6% a year before tax and 20.8% after, against 9.5%
for a Nifty index fund after tax. **Read it with care.** The universe is today's index members, the
companies that survived and grew, so every backtest drawn from it is flattered: the equal-weight basket
of all of them also made 21.2% a year. The factor rules added about 3.4% a year on top, beat the basket
in 61% of 12-month periods, and lagged badly in 2025 (-2% against +12% for the Nifty). Turnover is about
190% a year, and unlike a mutual fund you pay tax on every profitable sale. Index funds that track NSE's
factor indices (such as Nifty 200 Momentum 30 or Nifty Alpha Low-Volatility 30) run this kind of strategy
without that tax drag.

## Long-term goals and rebalancing

```bash
python -m stock_agent goal --target 2500000 --years 12 --current 200000 --monthly 15000
python -m stock_agent rebalance holdings.csv --target equity=70,debt=20,gold=10 --new-money 50000
```

*Goals* turns an amount needed (in today's money), a date and a monthly investment into a mix of equity,
debt and gold (more equity for longer goals and higher risk comfort), simulates 5,000 futures from ten
years of Nifty 50 ETF and gold ETF history (returns capped at 12% and 9% a year for planning; debt assumed
7%), and reports the chance of reaching the goal and the monthly amount for a 75% chance.

An optional crypto share (`--crypto 5`, or *Crypto share* in the app; at most 10%, only for goals five or
more years away) is taken out of equity and simulated from Bitcoin's price in rupees (BTC-INR): its 74%
volatility, its -73% worst fall and its correlation with stocks and gold. Its 68% a year over the last
decade is not used as a plan; the planning return is capped at 15% and reduced by the 30% tax on crypto
gains (10.5%). The result is shown next to the same plan without crypto. For a 12-year goal with ₹15,000
a month, 10% in crypto lowered the chance of success from 68% to 62%, widened both the good and the bad
case, and raised the monthly amount for a 75% chance from ₹16,000 to ₹16,900.

*Rebalance* prices what you own (symbol and quantity, or a value for FDs and funds), compares it with the
target mix and, when any type has drifted more than the band (5 points by default), lists what to sell
and buy. It also shows how to place new money so that nothing has to be sold. Selling can attract
capital-gains tax; the new-money route avoids it.

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
  universes.py  stock lists from NSE files (Nifty 50/100, F&O, all NSE) and the S&P 500; recent listings
  lists/        dated copies of those lists for offline use
  ipo.py        new listings since their IPO, and IPO news
  markets.py    crypto (daily) and intraday commodity (15-minute) scans with MCX lot sizing
  fund.py       model factor fund: momentum + low-volatility ranking, backtest with costs and tax
  planner.py    long-term goal planner (allocation, Monte Carlo, required monthly investment)
  rebalance.py  current vs target mix, full rebalance and new-money-only trades
  sizing.py     exact order: contract, lots for your capital/risk, stop-loss, targets, time exit
  lots.py       NSE lot sizes (live fo_mktlots.csv, cached daily, bundled snapshot fallback)
  tradetest.py  every past signal replayed as the real trade; strategy rules (strategy.json)
  lab.py        rule-variant search with a train/test split; validation.json holds the latest check
  paper.py      paper trading: virtual account, fills, charges, automatic stop/target/square-off
  tracker.py    track record: pick ledger, day-by-day outcome simulation, replay of past days
  kite.py       Zerodha Kite Connect: login, contract lookup, one-click LIMIT buy + GTT stop/target
  app.py        local web app server (standard library only) + desktop shortcut
  web/          the app's single-page interface
  llm.py        optional local Ollama narrative
  __main__.py   CLI (app / shortcut / screen / track / lab / research / trade / rules / news)
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
