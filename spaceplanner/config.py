"""Configuration loading with deep-merge overrides. Nothing in the engine is hard-coded."""
from __future__ import annotations
import copy
import json
import os
from typing import Any

import yaml

DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "config", "defaults.yaml")


def load_defaults() -> dict:
    with open(DEFAULTS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def deep_merge(base: dict, override: dict | None) -> dict:
    out = copy.deepcopy(base)
    if not override:
        return out
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(override_path: str | None = None, overrides: dict | None = None) -> dict:
    cfg = load_defaults()
    if override_path:
        with open(override_path, "r", encoding="utf-8") as f:
            data = json.load(f) if override_path.endswith(".json") else yaml.safe_load(f)
        cfg = deep_merge(cfg, data or {})
    if overrides:
        cfg = deep_merge(cfg, overrides)
    return cfg


def get(cfg: dict, path: str, default: Any = None) -> Any:
    cur: Any = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
