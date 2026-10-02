# Top 5 setups

*Market data to 2026-10-01 · 52 stocks scanned · 10 with an active setup that has a historical edge · holding period 5 trading days*

> Strategy check 2023-2026 (13,063 past trades, not used for tuning): 38% won, average -2.2% per option trade. No proven edge: treat these as research ideas, not trade signals.

| # | Stock | Trade | Setup | Signal | Success rate | Baseline | Stock history | Last 3y | Universe | Avg move | News | Score |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **HINDALCO.NS** | BUY CALL | Doji after downtrend | 2026-09-29 | **50%** | 42% | 51% of 105 | 49% of 37 | 40% | +5.98% | +0.18 (40) | 0.567 |
| 2 | **INDIGO.NS** | BUY CALL | Tweezer Bottom | 2026-09-29 | **48%** | 42% | 51% of 55 | 88% of 8 | 39% | +4.91% | +0.18 (40) | 0.550 |
| 3 | **AXISBANK.NS** | BUY CALL | Doji after downtrend | 2026-10-01 | **46%** | 40% | 47% of 101 | 39% of 33 | 40% | +1.85% | +0.23 (40) | 0.546 |
| 4 | **KOTAKBANK.NS** | BUY CALL | Bullish Engulfing | 2026-09-29 | **44%** | 40% | 48% of 40 | 57% of 14 | 38% | +4.04% | +0.29 (40) | 0.542 |
| 5 | **BHARTIARTL.NS** | BUY CALL | Doji after downtrend | 2026-10-01 | **47%** | 40% | 48% of 126 | 47% of 36 | 40% | +1.99% | +0.04 (40) | 0.482 |

## Orders (sized for ₹2,00,000 capital, 2% risk per trade)

**1. HINDALCO.NS**  
0 lots: 1 lot of HINDALCO 27-Oct-2026 940 CE (lot 700, ~₹29.90) risks ₹5,565 to the stop, above your ₹4,000 budget  
STOP: sell if HINDALCO trades below 927.34 (premium ~₹21.95)  
TARGET: book half at 963.61 (~₹42.65), rest at 978.12 (~₹53.00) · time exit 08-Oct  
Max loss at stop ₹0 · if premium goes to zero ₹0 · premium estimated (Black-Scholes, vol 27%); check the live quote before ordering · lot size from NSE (cached today)

**2. INDIGO.NS**  
0 lots: 1 lot of INDIGO 27-Oct-2026 4950 CE (lot 150, ~₹150.55) risks ₹8,130 to the stop, above your ₹4,000 budget  
STOP: sell if INDIGO trades below 4818.29 (premium ~₹96.35)  
TARGET: book half at 5097.31 (~₹248.50), rest at 5208.92 (~₹330.75) · time exit 08-Oct  
Max loss at stop ₹0 · if premium goes to zero ₹0 · premium estimated (Black-Scholes, vol 28%); check the live quote before ordering · lot size from NSE (cached today)

**3. AXISBANK.NS**  
0 lots: 1 lot of AXISBANK 27-Oct-2026 1220 CE (lot 625, ~₹36.15) risks ₹6,250 to the stop, above your ₹4,000 budget  
STOP: sell if AXISBANK trades below 1197.98 (premium ~₹26.15)  
TARGET: book half at 1245.78 (~₹52.30), rest at 1264.90 (~₹65.55) · time exit 08-Oct  
Max loss at stop ₹0 · if premium goes to zero ₹0 · premium estimated (Black-Scholes, vol 27%); check the live quote before ordering · lot size from NSE (cached today)

**4. KOTAKBANK.NS**  
0 lots: 1 lot of KOTAKBANK 27-Oct-2026 420 CE (lot 2000, ~₹9.50) risks ₹15,000 to the stop, above your ₹4,000 budget  
STOP: sell if KOTAKBANK trades below 396.46 (premium ~₹2.00)  
TARGET: book half at 451.19 (~₹33.95), rest at 473.08 (~₹55.05) · time exit 08-Oct  
Max loss at stop ₹0 · if premium goes to zero ₹0 · premium estimated (Black-Scholes, vol 21%); check the live quote before ordering · lot size from NSE (cached today)

**5. BHARTIARTL.NS**  
0 lots: 1 lot of BHARTIARTL 27-Oct-2026 1740 CE (lot 475, ~₹48.70) risks ₹5,748 to the stop, above your ₹4,000 budget  
STOP: sell if BHARTIARTL trades below 1719.14 (premium ~₹36.60)  
TARGET: book half at 1774.04 (~₹67.55), rest at 1795.99 (~₹82.85) · time exit 08-Oct  
Max loss at stop ₹0 · if premium goes to zero ₹0 · premium estimated (Black-Scholes, vol 24%); check the live quote before ordering · lot size from NSE (cached today)


Notes:

- **HINDALCO.NS** close 941.85, trend down: none
- **INDIGO.NS** close 4929.90, trend down: none
- **AXISBANK.NS** close 1217.10, trend down: edge faded: 39% in the last 3y
- **KOTAKBANK.NS** close 418.35, trend up: none
- **BHARTIARTL.NS** close 1741.10, trend down: none

*Success rate* is the share of past signals that made money as the actual trade (buy the option next morning, intraday stop, Target 1, time exit), blended with the same pattern's rate across the universe (small samples lean on the universe). *Baseline* is the same trade started on an ordinary day. *Avg move* is the average return on the option. *Last 3y* repeats the test on recent data only, as a check that the edge has not faded. *Score* adds up to ±0.10 for news that agrees or disagrees with the trade. Run `python -m stock_agent trade <TICKER>` for the strike, expiry and breakeven check.

_Statistical screen, not investment advice. A 60% setup still loses 4 times in 10._
