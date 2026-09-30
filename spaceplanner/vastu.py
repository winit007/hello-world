"""Vastu rule evaluation at building and unit level, with per-rule Strict / Preferred / Ignore modes."""
from __future__ import annotations
import math

from .config import get
from .site import zone_grid, compass_of_vector, COMPASS

ZONE_LABEL = {"N": "North", "NE": "North-East", "E": "East", "SE": "South-East", "S": "South", "SW": "South-West",
              "W": "West", "NW": "North-West", "C": "Centre (Brahmasthan)"}
ROOM_RULE = {"master": "master", "bedroom": "bedroom", "guest": "bedroom", "kitchen": "kitchen", "pooja": "pooja",
             "living": "living", "toilet": "toilet", "stair": "stair", "entrance": "main_entry"}


def rule_mode(rule: str, cfg: dict, default_mode: str, overrides: dict | None) -> str:
    if overrides and rule in overrides:
        return str(overrides[rule]).lower()
    return default_mode


def judge(rule: dict, zone: str) -> tuple[str, float]:
    if zone in rule.get("good", []):
        return ("pass", 1.0)
    if zone in rule.get("alt", []):
        return ("partial", 0.65)
    if zone in rule.get("bad", []):
        return ("fail", 0.0)
    return ("partial", 0.4)


def unit_rows(layout, cfg: dict, mode: str, overrides: dict | None = None, unit_label: str = "") -> list[dict]:
    rows = []
    rules = get(cfg, "vastu.rules", {})
    for r in layout.rooms.values():
        rule_key = ROOM_RULE.get(r.type)
        if not rule_key or rule_key not in rules:
            continue
        m = rule_mode(rule_key, cfg, mode, overrides)
        if m == "ignore":
            continue
        status, score = judge(rules[rule_key], r.zone)
        rows.append({"rule": rule_key, "subject": f"{unit_label} {r.spec.label}".strip(), "zone": r.zone, "zone_label": ZONE_LABEL[r.zone],
                     "expected": "/".join(rules[rule_key].get("good", [])), "status": status, "score": score,
                     "weight": float(rules[rule_key].get("weight", 1)), "mode": m, "level": "unit"})
    # brahmasthan
    m = rule_mode("brahmasthan_open", cfg, mode, overrides)
    if m != "ignore" and "brahmasthan_open" in rules:
        bad = [r.spec.label for r in layout.rooms.values() if r.type in ("toilet", "stair", "store") and r.zone == "C"]
        rows.append({"rule": "brahmasthan_open", "subject": f"{unit_label} centre".strip(), "zone": "C", "zone_label": ZONE_LABEL["C"],
                     "expected": "open / light use", "status": "fail" if bad else "pass", "score": 0.0 if bad else 1.0,
                     "weight": float(rules["brahmasthan_open"].get("weight", 1)), "mode": m, "level": "unit",
                     "note": ", ".join(bad) if bad else ""})
    # cook facing east: kitchen has an east-ish external wall to place the platform against
    k = next((r for r in layout.rooms.values() if r.type == "kitchen"), None)
    if k and rule_mode("kitchen", cfg, mode, overrides) != "ignore":
        want = get(cfg, "vastu.cook_facing", "E")
        faces = [compass_of_vector(*{"top": (0, 1), "bottom": (0, -1), "left": (-1, 0), "right": (1, 0)}[sd], layout.ctx.north_deg) for sd in k.external_sides]
        ok = want in faces or any(f in (want, f"N{want}", f"S{want}") for f in faces)
        rows.append({"rule": "cook_facing", "subject": f"{unit_label} cooking platform".strip(), "zone": "/".join(faces) or "-",
                     "zone_label": "", "expected": f"facing {want}", "status": "pass" if ok else "partial", "score": 1.0 if ok else 0.5,
                     "weight": 2.0, "mode": rule_mode("kitchen", cfg, mode, overrides), "level": "unit"})
    return rows


def building_rows(items: list[dict], plot_bounds, north_deg: float, cfg: dict, mode: str, overrides: dict | None = None) -> list[dict]:
    """items: [{"rule": "main_entry"|"stair"|"ug_tank"|..., "label": str, "x": float, "y": float}]"""
    zone = zone_grid(plot_bounds, north_deg)
    rules = get(cfg, "vastu.rules", {})
    rows = []
    for it in items:
        rule = rules.get(it["rule"])
        if not rule or "good" not in rule:
            continue
        m = rule_mode(it["rule"], cfg, mode, overrides)
        if m == "ignore":
            continue
        z = zone(it["x"], it["y"])
        status, score = judge(rule, z)
        rows.append({"rule": it["rule"], "subject": it["label"], "zone": z, "zone_label": ZONE_LABEL[z], "expected": "/".join(rule.get("good", [])),
                     "status": status, "score": score, "weight": float(rule.get("weight", 1)), "mode": m, "level": "building"})
    return rows


def mass_row(footprint_centroid, plot_centroid, north_deg: float, cfg: dict, mode: str, overrides: dict | None = None) -> dict | None:
    m = rule_mode("mass_south_west", cfg, mode, overrides)
    rule = get(cfg, "vastu.rules.mass_south_west")
    if m == "ignore" or not rule:
        return None
    dx, dy = footprint_centroid[0] - plot_centroid[0], footprint_centroid[1] - plot_centroid[1]
    if math.hypot(dx, dy) < 0.5:
        status, score, z = "partial", 0.5, "C"
    else:
        z = compass_of_vector(dx, dy, north_deg)
        status, score = ("pass", 1.0) if z in ("S", "SW", "W") else ("fail", 0.0) if z in ("N", "NE", "E") else ("partial", 0.5)
    return {"rule": "mass_south_west", "subject": "building mass vs plot centre", "zone": z, "zone_label": ZONE_LABEL.get(z, z),
            "expected": "S/SW/W", "status": status, "score": score, "weight": float(rule.get("weight", 1)), "mode": m, "level": "building"}


def summarise(rows: list[dict]) -> dict:
    tw = sum(r["weight"] for r in rows)
    score = round(100 * sum(r["weight"] * r["score"] for r in rows) / tw, 1) if tw else None
    strict_fail = [r for r in rows if r["mode"] == "strict" and r["status"] == "fail"]
    return {"score": score, "pass": sum(r["status"] == "pass" for r in rows), "partial": sum(r["status"] == "partial" for r in rows),
            "fail": sum(r["status"] == "fail" for r in rows), "strict_failures": [f"{r['subject']} in {r['zone_label']} (expected {r['expected']})" for r in strict_fail]}
