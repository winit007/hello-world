"""Doors and windows: placement on the realised layout plus the door/window schedule."""
from __future__ import annotations
from collections import defaultdict

from . import units as U
from .config import get
from .geometry import Rect, OPPOSITE
from .layout import Layout, Room, pick_door_host, door_host_candidates, _on_side

DOOR_BY_TYPE = {"master": "bedroom", "bedroom": "bedroom", "guest": "bedroom", "servant": "bedroom", "study": "bedroom",
                "toilet": "toilet", "kitchen": "kitchen", "balcony": "balcony", "utility": "kitchen"}
WINDOW_BY_TYPE = {"master": "bedroom", "bedroom": "bedroom", "guest": "bedroom", "servant": "bedroom", "living": "living",
                  "dining": "dining", "kitchen": "kitchen", "study": "study", "toilet": "toilet", "shop": "living", "office": "living"}
CORNER_OFFSET_FT = 1.0        # keeps door frames clear of corner columns


def _seg(room: Room, side: str, a: float, b: float) -> tuple:
    r = room.cell
    if side == "top":
        return (a, r.y2, b, r.y2)
    if side == "bottom":
        return (a, r.y, b, r.y)
    if side == "left":
        return (r.x, a, r.x, b)
    return (r.x2, a, r.x2, b)


def place_openings(layout: Layout, cfg: dict, main_door: bool = True, columns: list | None = None) -> dict:
    """Mutates rooms in ``layout`` adding ``door`` and ``windows``; returns the schedule.

    ``columns`` is an optional list of (x, y, w, d) column rectangles; doors and windows keep clear of them.
    """
    rooms = layout.rooms
    ctx = layout.ctx
    occupied: dict[tuple, list] = defaultdict(list)     # wall line -> [(start, end)]
    for r in rooms.values():
        r.door = None
        r.windows = []
    for (cx, cy, cw, cd) in columns or []:
        # a column blocks the wall lines passing through it
        for r in rooms.values():
            for side in ("top", "bottom"):
                yy = r.cell.y2 if side == "top" else r.cell.y
                if abs(yy - cy) <= cd / 2 + 0.05:
                    occupied[("y", round(yy, 2))].append((cx - cw / 2 - 0.25, cx + cw / 2 + 0.25))
            for side in ("left", "right"):
                xx = r.cell.x2 if side == "right" else r.cell.x
                if abs(xx - cx) <= cw / 2 + 0.05:
                    occupied[("x", round(xx, 2))].append((cy - cd / 2 - 0.25, cy + cd / 2 + 0.25))
    warnings: list[str] = []
    doors_cfg = get(cfg, "doors")
    win_cfg = get(cfg, "windows")
    ratio = float(get(cfg, "regulatory.light_ventilation.habitable_window_area_ratio", 0.1))
    toilet_min = float(get(cfg, "regulatory.light_ventilation.toilet_ventilator_min_sqft", 3))
    kitchen_min = float(get(cfg, "regulatory.light_ventilation.kitchen_window_min_sqft", 9))
    sill_w = U.parse_length(get(cfg, "construction.sill_height_window_ft", 3))
    sill_v = U.parse_length(get(cfg, "construction.sill_height_ventilator_ft", 6))
    door_w_default = U.parse_length(doors_cfg["internal"]["w"])

    def line_key(side: str, room: Room):
        r = room.cell
        return ("y", round(r.y2 if side == "top" else r.y, 2)) if side in ("top", "bottom") else ("x", round(r.x2 if side == "right" else r.x, 2))

    def free(key, s, e) -> bool:
        return all(e <= a + 0.05 or s >= b - 0.05 for a, b in occupied[key])

    def fit_interval(key, a, b, w, prefer_start: bool):
        """Find a free interval of width w inside [a, b], offset from the ends."""
        lo, hi = a + CORNER_OFFSET_FT, b - CORNER_OFFSET_FT
        if hi - lo < w:
            lo, hi = a + 0.25, b - 0.25
            if hi - lo < w:
                return None
        options = [(lo, lo + w), (hi - w, hi), ((lo + hi) / 2 - w / 2, (lo + hi) / 2 + w / 2)]
        if not prefer_start:
            options[0], options[1] = options[1], options[0]
        for s, e in options:
            if free(key, s, e):
                return (s, e)
        step = 0.5
        s = lo
        while s + w <= hi:
            if free(key, s, s + w):
                return (s, s + w)
            s += step
        return None

    # --- doors --------------------------------------------------------------
    for r in rooms.values():
        hosts = door_host_candidates(r, layout, door_w_default, cfg)
        host = hosts[0] if hosts else None
        if host is None:
            r.issues.append("no door position found")
            warnings.append(f"{r.spec.label}: could not place a door")
            continue
        if host == "ENTRY" and ctx.entry_via_stair:
            # the stair opens into the living room / passage
            circ = [(ln, nk, sd, a, b) for nk, (sd, ln, a, b) in r.neighbours.items() if rooms[nk].type in ("living", "passage", "foyer", "dining")]
            if not circ:
                continue
            ln, nk, sd, a, b = max(circ)
            spec = doors_cfg["internal"]
            w = U.parse_length(spec["w"])
            key = line_key(sd, r)
            iv = fit_interval(key, a, b, w, True)
            if iv is None:
                continue
            occupied[key].append(iv)
            r.door = {"host": nk, "side": sd, "seg": _seg(r, sd, *iv), "w": w, "h": U.parse_length(spec["h"]), "tag": spec["tag"], "sliding": False, "swing_into": nk}
            continue
        if host == "ENTRY":
            if not main_door:
                continue
            spec = doors_cfg["main_entry"]
            w = U.parse_length(spec["w"])
            side = ctx.entry_side
            a, b = (r.cell.x, r.cell.x2) if side in ("top", "bottom") else (r.cell.y, r.cell.y2)
            key = line_key(side, r)
            iv = fit_interval(key, a, b, w, prefer_start=False)
            if iv is None:
                warnings.append(f"{r.spec.label}: entry wall too short for the main door")
                continue
            occupied[key].append(iv)
            r.door = {"host": "ENTRY", "side": side, "seg": _seg(r, side, *iv), "w": w, "h": U.parse_length(spec["h"]), "tag": spec["tag"], "sliding": False, "swing_into": r.key}
            continue
        # try each candidate host until a door fits on the shared wall
        placed = False
        for host in hosts:
            side, length, a, b = r.neighbours[host]
            if r.type in ("passage", "foyer", "dining", "living") and rooms[host].type in ("passage", "foyer", "dining", "living"):
                r.door = {"host": host, "side": side, "seg": _seg(r, side, a + 0.5, b - 0.5), "w": length - 1.0, "h": U.parse_length(doors_cfg["internal"]["h"]),
                          "tag": "OP", "sliding": False, "swing_into": r.key, "opening": True}
                placed = True
                break
            dtype = DOOR_BY_TYPE.get(r.type, "internal")
            spec = doors_cfg[dtype]
            w = U.parse_length(spec["w"])
            key = line_key(side, r)
            hr = rooms[host].cell
            host_centre = hr.cx if side in ("top", "bottom") else hr.cy
            prefer_start = abs(a - host_centre) < abs(b - host_centre)
            iv = fit_interval(key, a, b, w, prefer_start)
            if iv is None:
                continue
            occupied[key].append(iv)
            r.door = {"host": host, "side": side, "seg": _seg(r, side, *iv), "w": w, "h": U.parse_length(spec["h"]),
                      "tag": spec["tag"], "sliding": bool(spec.get("sliding")), "swing_into": r.key}
            placed = True
            break
        if not placed:
            warnings.append(f"{r.spec.label}: shared walls too short for a door")
            r.issues.append("door does not fit")
        continue
        if False:
            r.door = {"host": host, "side": side, "seg": _seg(r, side, a + 0.5, b - 0.5), "w": length - 1.0, "h": U.parse_length(doors_cfg["internal"]["h"]),
                      "tag": "OP", "sliding": False, "swing_into": r.key, "opening": True}
            continue
        dtype = DOOR_BY_TYPE.get(r.type, "internal")
        spec = doors_cfg[dtype]
        w = U.parse_length(spec["w"])
        key = line_key(side, r)
        hr = rooms[host].cell
        host_centre = hr.cx if side in ("top", "bottom") else hr.cy
        prefer_start = abs(a - host_centre) < abs(b - host_centre)
        iv = fit_interval(key, a, b, w, prefer_start)
        if iv is None:
            warnings.append(f"{r.spec.label}: shared wall with {rooms[host].spec.label} too short for a door")
            r.issues.append("door does not fit")
            continue
        occupied[key].append(iv)
        r.door = {"host": host, "side": side, "seg": _seg(r, side, *iv), "w": w, "h": U.parse_length(spec["h"]),
                  "tag": spec["tag"], "sliding": bool(spec.get("sliding")), "swing_into": r.key}

    # --- windows ------------------------------------------------------------
    for r in rooms.values():
        if r.type in ("passage", "foyer", "store", "dress", "stair"):
            continue
        real_ext = [sd for sd in r.external_sides if _on_side(r.cell, ctx.rect, sd) and ctx.external.get(sd)]
        if not real_ext:
            if r.spec.habitable or r.type == "toilet":
                via = [i for i in r.issues if i.startswith("light via")]
                if not via and r.type == "toilet":
                    r.issues.append("internal toilet: mechanical exhaust + duct required")
                    warnings.append(f"{r.spec.label}: no external wall; provide exhaust fan and duct")
            continue
        wtype = WINDOW_BY_TYPE.get(r.type, "default")
        spec = win_cfg.get(wtype, win_cfg["default"])
        w0, h0 = U.parse_length(spec["w"]), U.parse_length(spec["h"])
        if r.type == "toilet":
            need = toilet_min
        elif r.type == "kitchen":
            need = max(ratio * r.clear.area, kitchen_min)
        elif r.spec.habitable:
            need = ratio * r.clear.area
        else:
            need = 0.0
        # longest external wall first
        real_ext.sort(key=lambda sd: -(r.clear.w if sd in ("top", "bottom") else r.clear.h))
        got = 0.0
        for sd in real_ext:
            wall_len = (r.cell.w if sd in ("top", "bottom") else r.cell.h) - 2 * CORNER_OFFSET_FT
            if wall_len < 1.5:
                continue
            w = w0
            if need > 0 and got + w * h0 < need:
                w = min(wall_len, max(w0, (need - got) / h0))
            key = line_key(sd, r)
            a, b = (r.cell.x, r.cell.x2) if sd in ("top", "bottom") else (r.cell.y, r.cell.y2)
            centre = (a + b) / 2
            iv = (centre - w / 2, centre + w / 2)
            if not free(key, *iv):
                iv2 = fit_interval(key, a, b, w, True)
                if iv2 is None:
                    continue
                iv = iv2
            occupied[key].append(iv)
            kind = "ventilator" if spec.get("ventilator") else "window"
            r.windows.append({"side": sd, "seg": _seg(r, sd, *iv), "w": iv[1] - iv[0], "h": h0, "tag": spec["tag"], "kind": kind,
                              "sill": sill_v if kind == "ventilator" else sill_w, "exhaust": bool(spec.get("exhaust"))})
            got += (iv[1] - iv[0]) * h0
            if got >= need - 1e-6:
                break
        if need > 0 and got < need - 1e-6:
            r.issues.append(f"window area {got:.0f} sq ft < required {need:.0f} sq ft")
            warnings.append(f"{r.spec.label}: window area {got:.0f} sq ft is below the required {need:.0f} sq ft ({int(ratio*100)}% of floor)")

    return {"schedule": schedule(layout, cfg), "warnings": warnings}


def schedule(layout: Layout, cfg: dict) -> dict:
    doors: dict[str, dict] = {}
    windows: dict[str, dict] = {}
    for r in layout.rooms.values():
        if r.door and not r.door.get("opening"):
            d = r.door
            key = f"{d['tag']} {U.ftin(d['w'])} x {U.ftin(d['h'])}"
            e = doors.setdefault(key, {"tag": d["tag"], "w": d["w"], "h": d["h"], "count": 0, "rooms": [], "sliding": d["sliding"]})
            e["count"] += 1
            e["rooms"].append(r.spec.label)
        for wd in r.windows:
            key = f"{wd['tag']} {U.ftin(wd['w'])} x {U.ftin(wd['h'])}"
            e = windows.setdefault(key, {"tag": wd["tag"], "w": wd["w"], "h": wd["h"], "count": 0, "rooms": [], "kind": wd["kind"], "sill": wd["sill"]})
            e["count"] += 1
            e["rooms"].append(r.spec.label)
    return {"doors": doors, "windows": windows}


def merge_schedules(schedules: list[dict]) -> dict:
    out = {"doors": {}, "windows": {}}
    for sc in schedules:
        for kind in ("doors", "windows"):
            for key, e in sc.get(kind, {}).items():
                m = out[kind].setdefault(key, {**e, "count": 0, "rooms": []})
                m["count"] += e["count"]
                m["rooms"] = sorted(set(m["rooms"]) | set(e["rooms"]))
    return out
