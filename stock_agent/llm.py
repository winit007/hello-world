"""Optional narrative layer using a *local* LLM through Ollama, so nothing leaves the machine.

If Ollama is not running the agent silently skips this step; every number in the report is
produced by the deterministic code, the model only writes the prose.
"""
from __future__ import annotations

import os

import requests

OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")


def available() -> bool:
    try:
        return requests.get(f"{OLLAMA_URL}/api/tags", timeout=2).ok
    except requests.RequestException:
        return False


def narrate(context: str, model: str = OLLAMA_MODEL) -> str | None:
    """Ask the local model for a short analyst-style summary of the computed facts."""
    if not available():
        return None
    prompt = (
        "You are an equity research assistant. Using ONLY the facts below, write a 5-sentence "
        "briefing: current technical bias, the strongest historical candlestick rule and its win "
        "rate, what the news sentiment says, and the main risk. Do not invent numbers.\n\n" + context
    )
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}},
            timeout=120,
        )
        r.raise_for_status()
        return r.json().get("response", "").strip() or None
    except requests.RequestException as exc:
        print(f"[llm] Ollama call failed: {exc}")
        return None
