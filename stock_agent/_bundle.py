"""Imports every module of the app, so the Windows program (PyInstaller) packs all of them.

Several modules are imported only when needed (inside functions or by name), which a packer cannot always see.
tests/test_publish.py checks that this list stays complete."""
# ruff: noqa: F401
from . import app
from . import backtest
from . import broker_base
from . import brokers
from . import checklist
from . import data
from . import fivepaisa
from . import fund
from . import groww
from . import ipo
from . import kite
from . import lab
from . import llm
from . import longterm
from . import lots
from . import markets
from . import news
from . import options
from . import paper
from . import patterns
from . import planner
from . import publish
from . import rebalance
from . import report
from . import robust
from . import screener
from . import sizing
from . import tracker
from . import tradecheck
from . import tradetest
from . import universes
from . import upstox
from . import yahoo
