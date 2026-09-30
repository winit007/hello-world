"""Site plan: gate, driveway, parking bays and external services placed in the setback areas."""
from __future__ import annotations
import math

from shapely.geometry import Polygon, box, Point, LineString
from shapely.ops import unary_union

from . import units as U
from .config import get
from .geometry import Rect
from .site import Plot, zone_grid, vector_of_compass

SERVICE_DEFS = {
    # name: (vastu rule, size getter, on_roof)
    "ug_tank":      ("ug_tank", "ug_tank", False),
    "oht":          ("oht", "oht", True),
    "dg_set":       ("dg_electrical", "dg", False),
    "transformer":  ("dg_electrical", "transformer", False),
    "meter_room":   ("dg_electrical", "meter", False),
    "fire_pump":    ("dg_electrical", "fire_pump", False),
    "fire_tank":    ("ug_tank", "fire_tank", False),
    "stp":          ("septic_stp", "stp", False),
    "septic_tank":  ("septic_stp", "septic", False),
    "rwh_pit":      ("ug_tank", "rwh", False),
    "guard_room":   (None, "guard", False),
    "garbage_room": ("septic_stp", "garbage", False),
    "society_office": (None, "office", False),
    "driver_toilet": ("toilet", "driver_toilet", False),
    "borewell":     ("ug_tank", "borewell", False),
}


def service_size(name: str, brief: dict, cfg: dict, n_flats: int, commercial_sqm: float = 0.0) -> tuple[float, float]:
    sv = get(cfg, "services")
    persons = n_flats * float(sv.get("persons_per_flat", 4.5))
    if name == "ug_tank" or name == "fire_tank":
        litres = persons * float(sv.get("ug_tank_litres_per_person", 135)) * float(sv.get("ug_tank_days", 2))
        if name == "fire_tank":
            litres = n_flats * float(get(cfg, "regulatory.fire.fire_tank_litres_per_flat", 1000))
        vol_cuft = litres / 28.3168
        depth = 8.0
        side = math.sqrt(max(vol_cuft / depth, 25))
        return (round(side + 2, 1), round(side + 2, 1))
    if name == "oht":
        litres = persons * float(sv.get("ug_tank_litres_per_person", 135)) * float(sv.get("oht_fraction_of_daily", 0.5))
        side = math.sqrt(max(litres / 28.3168 / 5.0, 16))
        return (round(side + 1, 1), round(side + 1, 1))
    if name == "dg_set":
        kva = float(brief.get("dg_kva", 25 + 5 * n_flats))
        a = max(float(sv.get("dg_room_min_sqft", 80)), kva * float(sv.get("dg_room_sqft_per_kva", 0.6)))
        return (round(math.sqrt(a * 1.5), 1), round(math.sqrt(a / 1.5), 1))
    if name == "transformer":
        return tuple(sv.get("transformer_yard_ft", [12, 12]))
    if name == "meter_room":
        a = float(sv.get("meter_room_sqft", 60)); return (round(math.sqrt(a * 1.5), 1), round(math.sqrt(a / 1.5), 1))
    if name == "fire_pump":
        a = float(sv.get("fire_pump_room_sqft", 150)); return (round(math.sqrt(a * 1.5), 1), round(math.sqrt(a / 1.5), 1))
    if name == "stp":
        a = max(100.0, n_flats * float(sv.get("stp_sqft_per_flat", 6))); return (round(math.sqrt(a * 1.5), 1), round(math.sqrt(a / 1.5), 1))
    if name == "septic_tank":
        a = max(40.0, persons / 10 * float(sv.get("septic_tank_sqft_per_10_users", 40))); return (round(math.sqrt(a * 2), 1), round(math.sqrt(a / 2), 1))
    if name == "rwh_pit":
        return tuple(sv.get("rwh_pit_ft", [6, 6]))
    if name == "guard_room":
        return tuple(sv.get("guard_room_ft", [8, 8]))
    if name == "garbage_room":
        return tuple(sv.get("garbage_room_ft", [6, 8]))
    if name == "society_office":
        a = float(sv.get("society_office_sqft", 120)); return (round(math.sqrt(a * 1.3), 1), round(math.sqrt(a / 1.3), 1))
    if name == "driver_toilet":
        return tuple(sv.get("driver_toilet_ft", [4, 6]))
    if name == "borewell":
        return (4.0, 4.0)
    return (6.0, 6.0)


def required_services(brief: dict, project_type: str, n_flats: int, high_rise: bool, lift: bool) -> list[str]:
    req = brief.get("services")
    if isinstance(req, dict):
        names = [k for k, v in req.items() if v]
    elif isinstance(req, list):
        names = list(req)
    else:
        names = ["ug_tank", "oht", "rwh_pit"]
        if project_type in ("apartment", "mixed", "commercial"):
            names += ["meter_room", "guard_room", "garbage_room", "dg_set"]
            if n_flats >= 8:
                names += ["stp" if n_flats >= 20 else "septic_tank", "society_office"]
            else:
                names += ["septic_tank"]
            if n_flats >= 20:
                names += ["transformer"]
        else:
            names += ["septic_tank"]
        if high_rise:
            names += ["fire_tank", "fire_pump"]
    return list(dict.fromkeys(names))


def plan_site(plot: Plot, envelope: Polygon, footprint: Rect, cfg: dict, brief: dict, n_flats: int, ecs_required: float,
              stilt: bool, high_rise: bool, lift: bool, vastu_mode: str = "preferred") -> dict:
    fp = box(footprint.x, footprint.y, footprint.x2, footprint.y2)
    open_area = plot.polygon.difference(fp)
    zone = zone_grid(plot.polygon.bounds, plot.north_deg)
    warnings: list[str] = []
    pk = get(cfg, "regulatory.parking")
    bay_w, bay_l = U.m_to_ft(pk["car_bay_m"][0]), U.m_to_ft(pk["car_bay_m"][1])
    drive_w = U.m_to_ft(pk["driveway_width_m"])
    taken: list[Polygon] = []
    underground = {"ug_tank", "septic_tank", "rwh_pit", "fire_tank", "borewell", "stp"}
    above_ground: list[Polygon] = []

    # --- gate + driveway ------------------------------------------------------
    ve = plot.edges[plot.vehicle_edge]
    ee = plot.edges[plot.entry_edge]
    nx, ny = ve.normal
    # gate at 1/4 along the vehicle edge from the corner farther from the second road (simple heuristic)
    t = 0.75 if plot.corner else 0.25
    gx, gy = ve.a[0] + (ve.b[0] - ve.a[0]) * t, ve.a[1] + (ve.b[1] - ve.a[1]) * t
    inward = (-nx, -ny)
    # driveway from the gate straight in until it meets the footprint (or crosses the plot)
    length = 1e4
    ray = LineString([(gx, gy), (gx + inward[0] * length, gy + inward[1] * length)])
    hit = ray.intersection(fp)
    stop = hit.bounds if not hit.is_empty else None
    d_len = math.hypot(stop[0] - gx, stop[1] - gy) if stop else min(ve.length, 40)
    if stop:
        # distance to the nearest hit point
        pts = [Point(c) for c in (hit.coords if hit.geom_type == "LineString" else [hit.bounds[:2]])]
        d_len = min(Point(gx, gy).distance(p) for p in pts) if pts else d_len
    drive = LineString([(gx, gy), (gx + inward[0] * d_len, gy + inward[1] * d_len)]).buffer(drive_w / 2, cap_style=2)
    drive = drive.intersection(plot.polygon)
    taken.append(drive)
    above_ground.append(drive)
    gate = {"x": gx, "y": gy, "width": drive_w, "edge": ve.index}
    pedestrian = {"x": ee.mid[0], "y": ee.mid[1], "edge": ee.index}

    # --- services -------------------------------------------------------------
    names = required_services(brief, brief.get("project_type", "house"), n_flats, high_rise, lift)
    rules = get(cfg, "vastu.rules", {})
    services = []
    minx, miny, maxx, maxy = plot.polygon.bounds
    for name in names:
        rule_key, _, on_roof = SERVICE_DEFS.get(name, (None, None, False))
        w, h = service_size(name, brief, cfg, n_flats)
        rule = rules.get(rule_key) if rule_key else None
        pref = list(rule.get("good", [])) + list(rule.get("alt", [])) if rule and vastu_mode != "ignore" else []
        avoid = list(rule.get("bad", [])) if rule and vastu_mode != "ignore" else []
        region = fp if on_roof else open_area
        clearance = U.parse_length(get(cfg, "services.transformer_clearance_ft", 10)) if name == "transformer" else 0.0
        best = None
        step = 2.0
        candidates = []
        y = miny
        while y + h <= maxy + 1e-6:
            x = minx
            while x + w <= maxx + 1e-6:
                cand = box(x, y, x + w, y + h)
                if region.contains(cand.buffer(-1e-6)):
                    z = zone(x + w / 2, y + h / 2)
                    rank = 0 if z in pref[:1] else 1 if z in pref else 3 if z in avoid else 2
                    if name == "guard_room":
                        rank = Point(gx, gy).distance(cand.centroid) / 10.0
                    candidates.append((rank, Point(gx, gy).distance(cand.centroid) if name != "guard_room" else 0, x, y, z))
                x += step
            y += step
        candidates.sort()
        for rank, _, x, y, z in candidates:
            cand = box(x, y, x + w, y + h)
            test = cand.buffer(clearance) if clearance else cand
            if any(test.intersects(tk) for tk in taken) or (clearance and test.intersects(fp) and not on_roof):
                continue
            best = (x, y, z, rank)
            break
        if best is None:
            warnings.append(f"No space found for {name.replace('_', ' ')} ({U.ftin(w)} x {U.ftin(h)}); reduce its size or the footprint.")
            services.append({"name": name, "placed": False, "w": w, "h": h, "preferred": pref[:1]})
            continue
        x, y, z, rank = best
        taken.append(box(x, y, x + w, y + h))
        if name not in underground and not on_roof:
            above_ground.append(box(x, y, x + w, y + h))
        status = "pass" if (not pref or z == pref[0]) else "partial" if z in pref else "fail" if z in avoid else "partial"
        services.append({"name": name, "placed": True, "rect": Rect(x, y, w, h), "zone": z, "preferred": pref[:1], "vastu": status,
                         "on_roof": on_roof, "rule": rule_key, "label": name.replace("_", " ").title()})
        if status == "fail":
            warnings.append(f"{name.replace('_', ' ').title()} placed in {z}; Vastu prefers {'/'.join(pref[:2])}. No compliant space was free.")

    # --- parking ----------------------------------------------------------------
    bays: list[Rect] = []
    park_region = open_area.difference(unary_union(above_ground)) if above_ground else open_area
    # stilt bays are laid out on the stilt floor plan itself (see apartment.plan_stilt_floor)
    # surface bays: sweep the open area with bays oriented along x then y
    for (bw, bl) in ((bay_w, bay_l), (bay_l, bay_w)):
        y = miny
        while y + bl <= maxy + 1e-6:
            x = minx
            while x + bw <= maxx + 1e-6:
                cand = box(x, y, x + bw, y + bl)
                if park_region.contains(cand.buffer(-1e-6)) and not any(cand.intersects(box(b.x, b.y, b.x2, b.y2)) for b in bays):
                    # a bay must touch the driveway or another bay's aisle: keep it simple, require distance to drive <= bay_l + 2
                    if cand.distance(drive) <= bay_l + 2.0:
                        bays.append(Rect(x, y, bw, bl))
                x += bw
            y += bl
    ecs_provided = len(bays)
    if ecs_provided < ecs_required:
        warnings.append(f"Parking: {ecs_provided} car bays fit, {math.ceil(ecs_required)} ECS required. Consider stilt or basement parking.")

    return {"gate": gate, "pedestrian_entry": pedestrian, "driveway": list(drive.exterior.coords) if not drive.is_empty and drive.geom_type == "Polygon" else [],
            "parking": bays, "ecs_required": ecs_required, "ecs_provided": ecs_provided, "services": services,
            "open_area_sqft": open_area.area, "warnings": warnings}
