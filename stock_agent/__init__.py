"""Offline-capable stock research agent.

Pipeline:  prices (cached)  ->  candlestick patterns  ->  backtest every pattern
           news RSS (cached) ->  sentiment            ->  combined outlook + ranked rules
"""

# The Android app runs pandas 2.1, the PC app usually a newer one. pandas 3 no longer forward-fills gaps inside
# pct_change (older versions did, and warn about it), so make every version behave the same way.
import pandas as _pd

if tuple(int(x) for x in _pd.__version__.split(".")[:2]) < (3, 0):
    def _no_fill(orig):
        def pct_change(self, periods=1, fill_method=None, *args, **kwargs):
            return orig(self, periods, fill_method, *args, **kwargs)
        return pct_change
    for _cls in (_pd.Series, _pd.DataFrame):
        _cls.pct_change = _no_fill(_cls.pct_change)

__version__ = "0.20.0"
