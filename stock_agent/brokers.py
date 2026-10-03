"""The brokers the app can place orders with. Each adapter shares broker_base's order flow."""
from __future__ import annotations

from pathlib import Path

from .broker_base import Broker, BrokerError, exit_now, place, preview, sync_one  # noqa: F401
from .fivepaisa import FivePaisa
from .groww import Groww
from .kite import Kite
from .upstox import Upstox

BROKERS: dict[str, type[Broker]] = {"kite": Kite, "upstox": Upstox, "groww": Groww, "fivepaisa": FivePaisa}
LABELS = {k: v.label for k, v in BROKERS.items()}


def make(key: str, home: Path) -> Broker:
    if key not in BROKERS:
        raise BrokerError(f"Unknown broker {key!r}")
    return BROKERS[key](home)
