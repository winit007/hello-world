"""Check my trade: the historical win rate of a trade you describe, for any kind of trade.

You give the instrument, direction, entry, stop-loss, target and how long you would hold. The same trade
(the same percentage distances to the stop and the target, the same holding time and, for options, the
same strike distance and days to expiry) is replayed from every past day of the instrument's history:

  win   the target is reached before the stop-loss, or at the end of the holding time the trade is up
        after costs. When the stop and the target fall inside one candle, the stop counts first.
  conditions like today   the same replay restricted to past days whose trend (price above or below its
        50-day average) and volatility (low / normal / high third) match today's.
  break-even win rate     the win rate this risk/reward needs to break even before costs.

Options are priced with Black-Scholes at the stock's own recent volatility (higher of 20- and 60-day,
plus 10%), because free history of NSE option prices does not exist; stop and target are levels on the
underlying price, as in Paper trading. Intraday trades replay 15-minute candles of the last 60 days.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

COSTS = {("stock", "delivery"): 0.0025, ("stock", "intraday"): 0.0006, ("option", "positional"): 0.02,
         ("option", "intraday"): 0.02, ("crypto", "positional"): 0.005, ("commodity", "intraday"): 0.0005,
         ("commodity", "positional"): 0.0005, ("crypto", "intraday"): 0.005}
BARS_PER_DAY = {"1d": 1, "15m": 25}          # NSE day ~ 25 fifteen-minute candles


def wilson(wins: int, n: int, z: float = 1.645) -> tuple[float, float]:
    if not n:
        return 0.0, 0.0
    p = wins / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(c - h, 0.0), min(c + h, 1.0)


def bearish(side: str, instrument: str, option_type: str | None) -> bool:
    """True when the trade gains as the price falls (short, bought put, sold call)."""
    short = side == "sell"
    if instrument == "option":
        return (option_type == "PE") != short
    return short


def _conditions(close: pd.Series) -> pd.DataFrame:
    sma = close.rolling(50, min_periods=40).mean()
    vol = close.pct_change().rolling(20).std()
    terc = vol.rolling(500, min_periods=120).rank(pct=True)
    return pd.DataFrame({"up": close > sma, "vol_band": np.select([terc < 1 / 3, terc < 2 / 3], [0, 1], 2)},
                        index=close.index).where(sma.notna() & terc.notna())


def check(df: pd.DataFrame, instrument: str, side: str, entry: float, stop: float | None, target: float | None,
          hold_bars: int, product: str = "positional", option: dict | None = None, interval: str = "1d",
          cost: float | None = None) -> dict:
    """Replay the trade from every past bar. `option` = {"type": "CE"/"PE", "strike", "days_to_expiry"}."""
    from .options import bs_price

    if side not in ("buy", "sell"):
        raise ValueError("Side must be buy or sell")
    if hold_bars < 1:
        raise ValueError("Hold for at least one candle")
    otype = (option or {}).get("type")
    bear = bearish(side, instrument, otype)
    if stop is not None and ((stop <= entry) if bear else (stop >= entry)):
        raise ValueError(f"The stop-loss must be {'above' if bear else 'below'} the entry price ({entry:,.2f}) for this trade")
    if target is not None and ((target >= entry) if bear else (target <= entry)):
        raise ValueError(f"The target must be {'below' if bear else 'above'} the entry price ({entry:,.2f}) for this trade")
    stop_d = abs(entry - stop) / entry if stop is not None else None
    tgt_d = abs(target - entry) / entry if target is not None else None
    cost = COSTS.get((instrument, product), 0.002) if cost is None else cost
    o, h, l, c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    n = len(df)
    if n < hold_bars + 80:
        raise ValueError("Not enough price history for this check")
    cond = _conditions(df["Close"])
    rets = df["Close"].pct_change()
    per_year = 252 * BARS_PER_DAY.get(interval, 1)
    short, long_ = (20, 60) if interval == "1d" else (100, 300)     # 60 days of 15-minute candles is ~1,000 bars
    hv = np.maximum(rets.rolling(short).std(), rets.rolling(long_, min_periods=short).std())
    iv = (hv * math.sqrt(per_year) * 1.1).to_numpy()
    if instrument == "option":
        if not option or not option.get("strike") or not option.get("days_to_expiry"):
            raise ValueError("Give the option's strike and expiry")
        moneyness = float(option["strike"]) / entry
        dte = float(option["days_to_expiry"])
        kind = "call" if otype == "CE" else "put"
    rows = []
    start = 60 if interval == "1d" else 100
    for i in range(start, n - hold_bars):
        s0 = c[i]
        if not s0 > 0:
            continue
        sl = (s0 * (1 + stop_d) if bear else s0 * (1 - stop_d)) if stop_d else None
        tp = (s0 * (1 - tgt_d) if bear else s0 * (1 + tgt_d)) if tgt_d else None
        outcome, exit_px, j_exit = "time", c[i + hold_bars], i + hold_bars
        for j in range(i + 1, i + hold_bars + 1):
            if sl is not None and ((h[j] >= sl) if bear else (l[j] <= sl)):
                outcome, exit_px, j_exit = "stop", (max(o[j], sl) if bear else min(o[j], sl)), j
                break
            if tp is not None and ((l[j] <= tp) if bear else (h[j] >= tp)):
                outcome, exit_px, j_exit = "target", (min(o[j], tp) if bear else max(o[j], tp)), j
                break
        if instrument == "option":
            vol = iv[i]
            if not vol or math.isnan(vol):
                continue
            k = moneyness * s0
            elapsed = (j_exit - i) / BARS_PER_DAY.get(interval, 1) * 7 / 5          # trading -> calendar days
            p0 = bs_price(s0, k, dte / 365, vol, kind, 0.065)
            p1 = bs_price(exit_px, k, max(dte - elapsed, 0.01) / 365, vol, kind, 0.065)
            if p0 < 0.05:
                continue
            ret = ((p1 - p0) if side == "buy" else (p0 - p1)) / p0 - cost
        else:
            ret = ((exit_px - s0) if not bear else (s0 - exit_px)) / s0 - cost
        rows.append((df.index[i], outcome, ret, j_exit - i))
    if not rows:
        raise ValueError("No past trades could be replayed")
    t = pd.DataFrame(rows, columns=["date", "outcome", "ret", "bars"]).set_index("date")
    t["win"] = t["ret"] > 0
    now = cond.iloc[-1]
    like = t.join(cond)
    if pd.notna(now["up"]):
        like = like[(like["up"] == now["up"]) & (like["vol_band"] == now["vol_band"])]
    else:
        like = like.iloc[0:0]

    def summary(x: pd.DataFrame) -> dict | None:
        if x.empty:
            return None
        w = int(x["win"].sum())
        lo, hi = wilson(w, len(x))
        wins, losses = x.loc[x["win"], "ret"], x.loc[~x["win"], "ret"]
        return {"trades": int(len(x)), "wins": w, "win_rate": w / len(x), "low": lo, "high": hi,
                "target_first": float((x["outcome"] == "target").mean()), "stop_first": float((x["outcome"] == "stop").mean()),
                "time_exit": float((x["outcome"] == "time").mean()), "avg_ret": float(x["ret"].mean()),
                "avg_win": float(wins.mean()) if len(wins) else None, "avg_loss": float(losses.mean()) if len(losses) else None,
                "median_bars_to_target": float(x.loc[x["outcome"] == "target", "bars"].median()) if (x["outcome"] == "target").any() else None,
                "recent_win_rate": float(x[x.index >= x.index[-1] - pd.Timedelta(days=365 if interval == "1d" else 14)]["win"].mean())}

    be = (stop_d / (stop_d + tgt_d)) if stop_d and tgt_d and instrument != "option" else None
    return {"all": summary(t), "like_today": summary(like), "breakeven": be, "cost": cost,
            "from": str(t.index[0])[:16], "to": str(t.index[-1])[:16], "interval": interval, "hold_bars": hold_bars,
            "today": {"trend": None if pd.isna(now["up"]) else ("up" if now["up"] else "down"),
                      "volatility": None if pd.isna(now["vol_band"]) else ["low", "normal", "high"][int(now["vol_band"])]},
            "stop_pct": stop_d, "target_pct": tgt_d, "bearish": bear}
