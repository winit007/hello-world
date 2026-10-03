"""Offline-capable stock research agent.

Pipeline:  prices (cached)  ->  candlestick patterns  ->  backtest every pattern
           news RSS (cached) ->  sentiment            ->  combined outlook + ranked rules
"""

__version__ = "0.13.0"
