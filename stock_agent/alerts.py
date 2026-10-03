"""Alerts: the app watches your positions while it runs and tells you when something needs you.

What it watches (every 5 minutes in market hours, while the app is open):
  stop-loss near / hit      a position within `near_pct` of its stop, or through it (Paper trading closes its own
                            positions; trades in My trades and holdings with a stop are only reported)
  target reached            a position at or past its target
  exit date                 a trade whose planned exit date has come
  price alerts              your own "tell me when X goes above / below Y" (each fires once)
  screener changes          once a day after the close, the screen you chose to watch: which stocks came in or left
  model fund rebalance      three days before a rebalance date of the fund settings you watch
Where it tells you: the bell in the app (and the browser's own notification while the page is open), Telegram if
you connect a bot, and Android's notification shade on Termux. Each alert is sent once a day at most.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

MAX_KEEP = 300


# ----------------------------------------------------------------------------- what needs attention
def position_events(items: list[dict], price_of, near_pct: float = 1.0, today: date | None = None) -> list[dict]:
    """items: {key, label, symbol, bearish, stop, target, exit_by, source}; levels are prices of `symbol`."""
    today = today or date.today()
    out = []
    for it in items:
        try:
            px = price_of(it["symbol"])
        except Exception:
            continue
        if px is None:
            continue
        bear, stop, tgt = bool(it.get("bearish")), it.get("stop"), it.get("target")
        where = f"{it['label']} ({it.get('source', '')}) at {px:,.2f}"
        if stop:
            hit = px >= stop if bear else px <= stop
            gap = (stop - px) / px if bear else (px - stop) / px
            if hit:
                out.append({"key": f"{it['key']}:stop-hit", "kind": "stop", "level": "high",
                            "title": f"Stop-loss hit: {it['label']}", "text": f"{where}, stop {stop:,.2f}. Exit if you have not already."})
            elif 0 <= gap <= near_pct / 100:
                out.append({"key": f"{it['key']}:stop-near", "kind": "stop", "level": "medium",
                            "title": f"Near the stop: {it['label']}", "text": f"{where}, {gap:.1%} from the stop {stop:,.2f}."})
        if tgt and ((px <= tgt) if bear else (px >= tgt)):
            out.append({"key": f"{it['key']}:target", "kind": "target", "level": "medium",
                        "title": f"Target reached: {it['label']}", "text": f"{where}, target {tgt:,.2f}. Book profit as planned."})
        if it.get("exit_by"):
            try:
                due = date.fromisoformat(str(it["exit_by"])[:10])
            except ValueError:
                due = None
            if due and today >= due:
                out.append({"key": f"{it['key']}:exit-date", "kind": "exit", "level": "medium",
                            "title": f"Exit date: {it['label']}", "text": f"The planned exit date {due:%d %b} has come. Sell as planned."})
    return out


def price_rule_events(rules: list[dict], price_of) -> tuple[list[dict], list[str]]:
    events, fired = [], []
    for r in rules:
        try:
            px = price_of(r["symbol"])
        except Exception:
            continue
        if px is None:
            continue
        if (r["op"] == "above" and px >= r["price"]) or (r["op"] == "below" and px <= r["price"]):
            fired.append(r["id"])
            events.append({"key": f"rule:{r['id']}", "kind": "price", "level": "medium",
                           "title": f"{r['symbol'].replace('.NS', '')} is {r['op']} {r['price']:,.2f}",
                           "text": f"Now {px:,.2f}." + (f" Note: {r['note']}" if r.get("note") else "")})
    return events, fired


def screen_change_event(prev: list[str], now: list[str], name: str = "your screen") -> dict | None:
    came, left = [t for t in now if t not in prev], [t for t in prev if t not in now]
    if not came and not left:
        return None
    bare = lambda xs: ", ".join(x.replace(".NS", "") for x in xs)
    text = (f"In: {bare(came)}. " if came else "") + (f"Out: {bare(left)}." if left else "")
    return {"key": f"screen:{date.today()}", "kind": "screen", "level": "low", "title": f"Picks changed in {name}", "text": text.strip()}


def rebalance_event(next_date: date, today: date | None = None, days: int = 3) -> dict | None:
    today = today or date.today()
    if 0 <= (next_date - today).days <= days:
        return {"key": f"rebalance:{next_date}", "kind": "rebalance", "level": "low", "title": "Model fund rebalance due",
                "text": f"Rebalance on {next_date:%a %d %b}: open Model fund and press Build fund for the new list."}
    return None


def next_period_end(today: date, freq: str) -> date:
    """Last weekday of the current month / quarter / half-year / year."""
    months = {"M": 1, "Q": 3, "H": 6, "Y": 12}[freq]
    end_month = ((today.month - 1) // months + 1) * months
    d = date(today.year + (end_month // 13), (end_month - 1) % 12 + 1, 1)
    d = (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# ----------------------------------------------------------------------------- the store
class Store:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
        except (OSError, ValueError):
            d = {}
        d.setdefault("items", [])
        d.setdefault("sent", {})
        d.setdefault("rules", [])
        d.setdefault("config", {"enabled": True, "near_pct": 1.0})
        return d

    def save(self, d: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1))
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)          # holds the Telegram bot token
        except OSError:
            pass

    def add(self, d: dict, events: list[dict], now: datetime | None = None) -> list[dict]:
        """Keep the new ones (each key once a day) and return them."""
        now = now or datetime.now()
        day = now.date().isoformat()
        d["sent"] = {k: v for k, v in d["sent"].items() if v >= (now.date() - timedelta(days=7)).isoformat()}
        new = []
        for e in events:
            if d["sent"].get(e["key"]) == day:
                continue
            d["sent"][e["key"]] = day
            item = {**e, "id": uuid.uuid4().hex[:8], "time": now.isoformat(timespec="seconds"), "read": False}
            d["items"].insert(0, item)
            new.append(item)
        d["items"] = d["items"][:MAX_KEEP]
        return new


# ----------------------------------------------------------------------------- sending
def telegram_send(token: str, chat: str, text: str) -> None:
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=15,
                      json={"chat_id": chat, "text": text, "disable_web_page_preview": True})
    if not r.ok:
        raise RuntimeError(f"Telegram said: {r.json().get('description', r.status_code) if r.headers.get('content-type', '').startswith('application/json') else r.status_code}")


def telegram_chat_id(token: str) -> str | None:
    """The chat that last wrote to the bot (send it /start first)."""
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
    if not r.ok:
        raise RuntimeError("Telegram did not accept this bot token")
    for u in reversed(r.json().get("result", [])):
        chat = (u.get("message") or u.get("my_chat_member") or {}).get("chat")
        if chat:
            return str(chat["id"])
    return None


def termux_available() -> bool:
    return bool(shutil.which("termux-notification"))


def deliver(new: list[dict], config: dict) -> list[str]:
    """Send new alerts to the outside channels; returns problems (shown in the app)."""
    problems = []
    for e in new:
        line = f"{e['title']}\n{e['text']}"
        if config.get("telegram_token") and config.get("telegram_chat"):
            try:
                telegram_send(config["telegram_token"], config["telegram_chat"], "📈 " + line)
            except Exception as exc:
                problems.append(f"Telegram: {exc}")
        if config.get("termux", True) and termux_available():
            try:
                subprocess.run(["termux-notification", "--title", e["title"], "--content", e["text"], "--group", "stock-agent"],
                               timeout=10, check=False)
            except Exception as exc:
                problems.append(f"Termux: {exc}")
    return problems
