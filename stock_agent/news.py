"""Fetch recent headlines from free RSS feeds, cache them, and score sentiment offline (VADER)."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

from .data import DEFAULT_CACHE

FEEDS = {
    "google": "https://news.google.com/rss/search?q={q}+stock&hl=en-US&gl=US&ceid=US:en",
    "yahoo": "https://feeds.finance.yahoo.com/rss/2.0/headline?s={t}&region=US&lang=en-US",
}

# Institutional-holding filing spam ("12,345 shares bought by Foo Capital LLC") is not news.
NOISE = re.compile(
    r"(shares?|stake|position|holdings?) .*\b(bought|acquired|purchased|sold|unloaded|trimmed|raised|"
    r"lowered|increased|decreased|boosted|cut|added|has \$)\b|"
    r"\b(buys|sells|purchases|acquires|trims|raises|lowers|boosts|increases|decreases)\b .*\b(shares|stake|position|holdings)\b|"
    r"makes new .*investment in|new (stake|position) in|\bstake in\b .*\b(llc|inc\.?|lp|ltd|plc|gmbh|corp)\b",
    re.I,
)

# Event words that usually move a stock; used to tag headlines with a theme.
THEMES = {
    "earnings": ["earnings", "revenue", "profit", "eps", "quarter", "results", "guidance"],
    "analyst": ["upgrade", "downgrade", "price target", "analyst", "rating", "overweight", "underweight"],
    "deal": ["acquisition", "acquire", "merger", "buyout", "deal", "partnership"],
    "legal/regulatory": ["lawsuit", "antitrust", "regulator", "probe", "investigation", "fine", "sec "],
    "product": ["launch", "unveil", "product", "release", "chip", "model"],
    "macro": ["fed", "rates", "inflation", "tariff", "recession", "economy"],
    "insider/capital": ["buyback", "dividend", "insider", "stake", "offering", "split"],
}


@dataclass
class Headline:
    title: str
    link: str
    published: str          # ISO date
    source: str
    sentiment: float = 0.0  # VADER compound, -1..1
    themes: str = ""

    @property
    def label(self) -> str:
        if self.sentiment >= 0.2:
            return "positive"
        if self.sentiment <= -0.2:
            return "negative"
        return "neutral"


def _analyzer():
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    an = SentimentIntensityAnalyzer()
    # VADER is general purpose; teach it the words finance headlines lean on.
    an.lexicon.update({
        "upgrade": 2.0, "upgrades": 2.0, "upgraded": 2.0, "downgrade": -2.0, "downgrades": -2.0,
        "downgraded": -2.0, "beat": 1.5, "beats": 1.5, "miss": -1.5, "misses": -1.5, "missed": -1.5,
        "bullish": 2.0, "bearish": -2.0, "rally": 1.5, "rallies": 1.5, "surge": 1.5, "surges": 1.5,
        "soar": 1.5, "soars": 1.5, "plunge": -2.0, "plunges": -2.0, "slump": -1.5, "slumps": -1.5,
        "tumble": -1.5, "tumbles": -1.5, "sink": -1.5, "sinks": -1.5, "record high": 2.0,
        "all-time high": 2.0, "lawsuit": -1.5, "probe": -1.0, "recall": -1.5, "buyback": 1.5,
        "outperform": 1.5, "underperform": -1.5, "overweight": 1.0, "underweight": -1.0,
        "guidance cut": -2.0, "raises guidance": 2.0, "layoffs": -1.0,
    })
    return an


def _themes_for(title: str) -> str:
    t = title.lower()
    return ", ".join(k for k, words in THEMES.items() if any(w in t for w in words))


def fetch_headlines(ticker: str, company: str | None = None, limit: int = 40) -> list[Headline]:
    import feedparser

    q = quote_plus(company or ticker)
    urls = [FEEDS["google"].format(q=q), FEEDS["yahoo"].format(t=ticker)]
    seen, out = set(), []
    for url in urls:
        try:
            feed = feedparser.parse(url)
        except Exception as exc:  # pragma: no cover - network dependent
            print(f"[news] failed {url}: {exc}")
            continue
        for e in feed.entries:
            title = (e.get("title") or "").strip()
            publisher = None
            # Google News formats titles as "Headline - Publisher"; split so the publisher name
            # never leaks into sentiment ("The Motley Fool" is not bad news) or dedupe keys.
            if " - " in title and "google" in url:
                title, publisher = title.rsplit(" - ", 1)
                title, publisher = title.strip(), publisher.strip()
            key = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
            if not title or key in seen or NOISE.search(title):
                continue
            seen.add(key)
            ts = e.get("published_parsed") or e.get("updated_parsed")
            published = (
                datetime.fromtimestamp(time.mktime(ts), tz=timezone.utc).date().isoformat() if ts else ""
            )
            source = e.get("source", {}).get("title") if isinstance(e.get("source"), dict) else None
            source = source or publisher or ("Yahoo" if "yahoo" in url else "Google News")
            out.append(Headline(title, e.get("link", ""), published, source))
    out.sort(key=lambda h: h.published, reverse=True)
    return out[:limit]


def score(headlines: list[Headline]) -> list[Headline]:
    an = _analyzer()
    for h in headlines:
        h.sentiment = an.polarity_scores(h.title)["compound"]
        h.themes = _themes_for(h.title)
    return headlines


def _cache_path(cache_dir: Path, ticker: str) -> Path:
    return cache_dir / "news" / f"{ticker.upper()}.json"


def load_news(ticker: str, company: str | None = None, offline: bool = False, cache_dir: Path = DEFAULT_CACHE) -> list[Headline]:
    path = _cache_path(cache_dir, ticker)
    if not offline:
        try:
            headlines = score(fetch_headlines(ticker, company))
            if headlines:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps([asdict(h) for h in headlines], indent=1))
                return headlines
        except Exception as exc:  # pragma: no cover
            print(f"[news] online fetch failed ({exc}); trying cache")
    if path.exists():
        return [Headline(**d) for d in json.loads(path.read_text())]
    return []


def summarize(headlines: list[Headline]) -> dict:
    if not headlines:
        return {"count": 0, "mean": 0.0, "positive": 0, "negative": 0, "neutral": 0, "themes": {}}
    s = [h.sentiment for h in headlines]
    themes: dict[str, int] = {}
    for h in headlines:
        for t in filter(None, h.themes.split(", ")):
            themes[t] = themes.get(t, 0) + 1
    return {
        "count": len(headlines),
        "mean": sum(s) / len(s),
        "positive": sum(1 for h in headlines if h.label == "positive"),
        "negative": sum(1 for h in headlines if h.label == "negative"),
        "neutral": sum(1 for h in headlines if h.label == "neutral"),
        "themes": dict(sorted(themes.items(), key=lambda kv: -kv[1])),
    }
