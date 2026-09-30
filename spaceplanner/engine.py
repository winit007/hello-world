"""Engine: brief -> ranked layout options (site, floors, units, areas, Vastu, structure, schedules)."""
from __future__ import annotations
import math
import copy
from dataclasses import asdict

from shapely.geometry import box

from . import units as U
from .config import load_config, get, deep_merge
from .geometry import Rect
from .site import build_plot, apply_setbacks, regulatory_summary, largest_inscribed_rect, zone_grid, compass_of_vector
from .layout import UnitContext, Node, RoomSpec, build_programme, realise, check_hard, score_layout, SIDES
from .openings import place_openings, merge_schedules
from .apartment import (UnitPlanner, FloorPlan, UnitPlan, plan_residential_floor, plan_house_floor, plan_commercial_floor,
                        plan_stilt_floor, wall_thicknesses, unit_target_carpet)
from .structure import column_grid
from .siteplan import plan_site
from . import vastu as V

GOALS = ("max_carpet", "vastu", "balanced", "max_units", "premium")


def _rect(r: Rect | None) -> dict | None:
    return None if r is None else {"x": round(r.x, 3), "y": round(r.y, 3), "w": round(r.w, 3), "h": round(r.h, 3)}


def _coords(poly) -> list:
    return [[round(x, 3), round(y, 3)] for x, y in poly.exterior.coords]


def resolve_goal(project: dict, cfg: dict) -> tuple[str, dict]:
    goal = str(project.get("goal", "balanced")).lower().replace(" ", "_")
    weights = project.get("goal_weights")
    if isinstance(weights, dict) and weights:
        total = sum(float(v) for v in weights.values()) or 1
        w = {k: float(v) * 100 / total for k, v in weights.items()}
        return ("custom", w)
    if goal not in GOALS:
        goal = "balanced"
    return (goal, dict(get(cfg, f"scoring.goals.{goal}")))


# ---------------------------------------------------------------------------
# Units of the building programme
# ---------------------------------------------------------------------------
def unit_mix_per_floor(prog: dict, plate: Rect, cfg: dict, regulatory: dict, floors: int, goal: str) -> tuple[list[str], list[str]]:
    """Return the ordered list of unit types on a typical floor and warnings."""
    warnings = []
    mix = prog.get("unit_mix") or {}
    per_floor = prog.get("flats_per_floor")
    t_ext, _ = wall_thicknesses(cfg)
    plate_area = plate.area
    if mix:
        if all(isinstance(v, (int, float)) and v <= 1.0 for v in mix.values()) and abs(sum(mix.values()) - 1.0) < 0.05:
            # percentages -> counts by area
            avg = sum(unit_target_carpet(t, cfg) * v for t, v in mix.items())
            total_units = per_floor or max(1, int(plate_area * 0.78 / avg))
            counts = {t: max(0, round(v * total_units)) for t, v in mix.items()}
            if sum(counts.values()) == 0:
                counts[max(mix, key=mix.get)] = 1
        else:
            counts = {t: int(v) for t, v in mix.items()}
            if prog.get("target_total_flats") and floors:
                # counts are for the whole building -> per floor
                if sum(counts.values()) > (per_floor or 8):
                    counts = {t: max(1, round(v / floors)) for t, v in counts.items()}
    else:
        # maximise: fill the plate with 2BHKs (or the single type requested)
        t = prog.get("default_unit_type", "2bhk")
        n = per_floor or max(1, int(plate_area * 0.78 / (unit_target_carpet(t, cfg) * 1.15)))
        counts = {t: n}
    types: list[str] = []
    for t, c in counts.items():
        if t not in (get(cfg, "rooms.unit_programmes") or {}):
            warnings.append(f"Unknown unit type {t}; skipped")
            continue
        types += [t] * int(c)
    if per_floor and len(types) > per_floor:
        types = types[:per_floor]
        warnings.append(f"Unit mix trimmed to the flats-per-floor limit of {per_floor}.")
    need = sum(unit_target_carpet(t, cfg) * 1.18 for t in types)
    if need > plate_area:
        while types and sum(unit_target_carpet(t, cfg) * 1.18 for t in types) > plate_area:
            types.pop()
        warnings.append(f"Plate ({plate_area:,.0f} sq ft) cannot hold the requested mix; reduced to {len(types)} flats per floor.")
    if goal == "max_units" and types:
        # try adding one more of the smallest type if it fits
        smallest = min(set(types), key=lambda t: unit_target_carpet(t, cfg))
        if sum(unit_target_carpet(t, cfg) * 1.18 for t in types + [smallest]) <= plate_area:
            types.append(smallest)
    # order: bigger flats to the front row (road side) for premium/balanced, else as given
    types.sort(key=lambda t: -unit_target_carpet(t, cfg))
    return types, warnings


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def unit_to_dict(up: UnitPlan, cfg: dict) -> dict:
    d = {"label": up.label, "type": up.unit_type, "rect": _rect(up.rect), "clear": _rect(up.ctx.rect), "entry_side": up.ctx.entry_side,
         "external": dict(up.ctx.external), "error": up.error, "mirror_of": up.mirror_of, "rooms": [], "warnings": [],
         "zone_bounds": list(up.ctx.zone_bounds) if up.ctx.zone_bounds else None, "entry_via_stair": up.ctx.entry_via_stair}
    if not up.layout:
        d.update({"carpet_sqft": 0, "balcony_sqft": 0, "builtup_sqft": up.rect.area, "score": 0, "scores": {}, "relaxed": [], "tree": None})
        return d
    lay = up.layout
    bal = 0.0
    for r in lay.rooms.values():
        rd = {"key": r.key, "type": r.type, "label": r.spec.label, "cell": _rect(r.cell), "clear": _rect(r.clear), "zone": r.zone,
              "external_sides": list(r.external_sides), "door": r.door, "windows": r.windows, "area_sqft": round(r.clear.area, 1),
              "w": round(r.clear.w, 3), "h": round(r.clear.h, 3), "size_label": f"{U.ftin(r.clear.w)} x {U.ftin(r.clear.h)}", "issues": list(r.issues),
              "min_label": f"{U.ftin(r.spec.min_long)} x {U.ftin(r.spec.min_short)}" if r.spec.min_long else ""}
        if r.type == "balcony":
            bal += r.cell.area
        d["rooms"].append(rd)
    factor = float(get(cfg, "units.super_builtup_factor", 1.33))
    carpet = lay.ctx.rect.area - bal
    builtup = up.rect.area
    d.update({"carpet_sqft": round(carpet, 1), "balcony_sqft": round(bal, 1), "builtup_sqft": round(builtup, 1),
              "super_builtup_sqft": round(builtup * factor, 1), "score": lay.total, "scores": {k: v for k, v in lay.scores.items() if k != "vastu_detail"},
              "vastu_detail": lay.scores.get("vastu_detail", {}), "relaxed": list(lay.relaxed), "violations": list(lay.violations),
              "tree": lay.tree.to_dict(), "warnings": list(up.openings.get("warnings", [])), "schedule": up.openings.get("schedule", {})})
    return d


def floor_cells(fp: FloorPlan) -> list:
    room_cells = []
    for up in fp.units:
        if up.layout:
            room_cells += [r.cell for r in up.layout.rooms.values()]
    for er in fp.extra_rooms:
        room_cells.append(er["rect"])
    for name_, r in fp.core.items():
        if name_ != "block":
            room_cells.append(r)
    return room_cells


def floor_to_dict(fp: FloorPlan, cfg: dict, name: str, level: int, repeat: int, kind: str, grid: dict | None = None) -> dict:
    t_ext, t_int = wall_thicknesses(cfg)
    room_cells = floor_cells(fp)
    if grid is None:
        grid = column_grid(fp.plate, room_cells, cfg, kind=get(cfg, "_column_kind", "house"))
    cols = [(c["x"], c["y"], c["w"], c["d"]) for c in grid["columns"]]
    # re-place doors and windows so they keep clear of the (stacked) columns
    for up in fp.units:
        if up.layout:
            up.openings = place_openings(up.layout, cfg, main_door=True, columns=cols)
    doors = [r.door["seg"] for up in fp.units if up.layout for r in up.layout.rooms.values() if r.door]
    # column inside a room of this floor? (grid is shared by all floors)
    from .structure import wall_segments, TOL
    walls = wall_segments(room_cells, fp.plate)
    floating = 0
    for c in grid["columns"]:
        on = any(abs(wx - c["gx"]) < TOL and y1 - TOL <= c["gy"] <= y2 + TOL for wx, y1, y2 in walls["x"]) or \
             any(abs(wy - c["gy"]) < TOL and x1 - TOL <= c["gx"] <= x2 + TOL for wy, x1, x2 in walls["y"])
        if not on:
            floating += 1
    fwarn = [f"{floating} column(s) of the shared grid fall inside rooms on this floor; align walls or accept exposed columns."] if floating else []
    units = [unit_to_dict(u, cfg) for u in fp.units]
    factor = float(get(cfg, "units.super_builtup_factor", 1.33))
    builtup = fp.plate.area
    unit_builtup = sum(u["builtup_sqft"] for u in units)
    carpet = sum(u["carpet_sqft"] for u in units)
    common = builtup - unit_builtup
    return {"name": name, "level": level, "kind": kind, "repeat": repeat, "plate": _rect(fp.plate), "wall_ext_ft": t_ext, "wall_int_ft": t_int,
            "units": units, "core": {k: _rect(v) for k, v in fp.core.items()}, "corridor": _rect(fp.corridor),
            "shafts": [{"rect": _rect(s["rect"]), "serves": s["serves"], "kind": s["kind"]} for s in fp.shafts],
            "extra_rooms": [{"label": e["label"], "type": e["type"], "rect": _rect(e["rect"])} for e in fp.extra_rooms],
            "parking": [_rect(b) for b in fp.parking],
            "columns": {"grid_x": grid["grid_x"], "grid_y": grid["grid_y"], "labels_x": grid["labels_x"], "labels_y": grid["labels_y"],
                        "size_in": grid["column_size_in"], "regularity": grid["regularity"],
                        "columns": [{"id": c["id"], "x": c["x"], "y": c["y"], "gx": c["gx"], "gy": c["gy"], "w": c["w"], "d": c["d"]} for c in grid["columns"]]},
            "columns_csv": grid["csv"], "warnings": list(fp.warnings) + fwarn,
            "areas": {"builtup_sqft": round(builtup, 1), "unit_builtup_sqft": round(unit_builtup, 1), "carpet_sqft": round(carpet, 1),
                      "common_sqft": round(common, 1), "super_builtup_sqft": round(unit_builtup * factor, 1),
                      "efficiency": round(carpet / (unit_builtup * factor), 3) if unit_builtup else 0.0}}


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------
def generate_options(brief: dict, n_options: int | None = None) -> dict:
    cfg = load_config(overrides=brief.get("config"))
    project = brief.get("project", {}) or {}
    site = brief.get("site", {}) or {}
    reg_in = brief.get("regulatory", {}) or {}
    prog = brief.get("programme", {}) or {}
    rooms_in = brief.get("rooms", {}) or {}
    ptype = str(project.get("type", "house")).lower().replace(" ", "_").replace("-", "_")
    ptype = {"independent_house": "house", "mixed_use": "mixed"}.get(ptype, ptype)
    goal, weights = resolve_goal(project, cfg)
    vastu_mode = str(project.get("vastu", get(cfg, "vastu.strictness_default", "preferred"))).lower()
    vastu_over = project.get("vastu_overrides") or {}
    n_opts = n_options or int(get(cfg, "scoring.options_returned", 4))
    warnings: list[str] = []

    # --- site & regulation --------------------------------------------------
    plot = build_plot(site, cfg)
    land = U.LandUnits(get(cfg, "units.kattha_sqft", 1361.25), get(cfg, "units.dhur_per_kattha", 20))
    floors = int(prog.get("floors", get(cfg, "building.floors_default", 1)))
    stilt = bool(prog.get("stilt", False))
    basement = bool(prog.get("basement", False))
    f2f = U.parse_length(prog.get("floor_to_floor", get(cfg, "building.floor_to_floor_ft", 10)))
    if reg_in.get("authority"):
        cfg["regulatory"]["authority"] = reg_in["authority"]
    reg = regulatory_summary(plot, cfg, floors, f2f, stilt, basement)
    for k in ("far", "far_permitted"):
        if reg_in.get(k) is not None:
            reg["far_permitted"] = float(reg_in[k]); reg["permitted_builtup_sqft"] = reg["far_permitted"] * plot.area
    if reg_in.get("coverage_pct") is not None:
        reg["coverage_pct_max"] = float(reg_in["coverage_pct"]); reg["max_footprint_sqft"] = reg["coverage_pct_max"] / 100 * plot.area
    if reg_in.get("max_height") is not None:
        reg["max_height_ft"] = U.parse_length(reg_in["max_height"])
    if reg_in.get("max_floors") is not None and floors > int(reg_in["max_floors"]):
        warnings.append(f"Requested {floors} floors exceeds the permitted {reg_in['max_floors']}; capped.")
        floors = int(reg_in["max_floors"])
    total_floors = floors + (1 if stilt else 0)
    height_ft = reg["building_height_ft"]
    sb = apply_setbacks(plot, height_ft, cfg, overrides=reg_in.get("setbacks"))
    envelope = sb["envelope"]
    warnings += reg["warnings"] + sb["warnings"]
    lift_required = reg["lift_required"] or bool(prog.get("lift", False))
    cfg["_lift_required"] = lift_required
    cfg["_lifts_per_core"] = int(prog.get("lifts_per_core", 1))
    cfg["_column_kind"] = "house" if ptype == "house" else "apartment"

    # footprint: largest inscribed rectangle, trimmed to ground coverage
    x, y, w, h = largest_inscribed_rect(envelope, step=1.0)
    plate = Rect(x, y, w, h)
    max_fp = reg["max_footprint_sqft"]
    front_edge = plot.edges[plot.entry_edge]
    front_side = _front_side(plate, front_edge)
    if plate.area > max_fp:
        scale = max_fp / plate.area
        # trim from the rear (away from the front road) to keep the frontage
        if front_side in ("bottom", "top"):
            nh = plate.h * scale
            plate = Rect(plate.x, plate.y if front_side == "bottom" else plate.y2 - nh, plate.w, nh)
        else:
            nw = plate.w * scale
            plate = Rect(plate.x if front_side == "left" else plate.x2 - nw, plate.y, nw, plate.h)
        warnings.append(f"Footprint trimmed to {max_fp:,.0f} sq ft to respect {reg['coverage_pct_max']}% ground coverage.")
    plate = Rect(round(plate.x, 2), round(plate.y, 2), math.floor(plate.w * 2) / 2, math.floor(plate.h * 2) / 2)
    # avoid obstacles (trees, poles): trim the plate away from any obstacle rectangle
    for ob in plot.obstacles:
        try:
            ox, oy = U.parse_length(ob.get("x", 0)), U.parse_length(ob.get("y", 0))
            orad = U.parse_length(ob.get("radius", 3))
        except Exception:
            continue
        if plate.contains_point(ox, oy):
            warnings.append(f"Obstacle '{ob.get('name', 'obstacle')}' lies inside the footprint; footprint reduced to avoid it.")
            if abs(ox - plate.x) < abs(plate.x2 - ox):
                plate = Rect(ox + orad, plate.y, plate.x2 - ox - orad, plate.h)
            else:
                plate = Rect(plate.x, plate.y, ox - orad - plate.x, plate.h)

    plot_bounds = plot.polygon.bounds
    zone = zone_grid(plot_bounds, plot.north_deg)

    # --- options -----------------------------------------------------------------
    seed0 = int(get(cfg, "scoring.random_seed", 7))
    variants = []
    for k in range(max(n_opts * 2, 4)):
        variants.append({"seed": seed0 + 13 * k, "core_pos": ["auto", "front", "rear", "auto"][k % 4], "rank": k // 2 if ptype == "house" else 0})
    options = []
    for vi, var in enumerate(variants):
        opt = _build_option(vi, var, ptype, plot, envelope, plate, front_side, floors, stilt, f2f, reg, prog, rooms_in, cfg, goal, weights,
                            vastu_mode, vastu_over, brief, land, zone, sb)
        if opt is not None:
            options.append(opt)
        if len([o for o in options if o["feasible"]]) >= n_opts and vi >= n_opts:
            break
    # dedupe by signature and rank
    seen = set()
    uniq = []
    for o in sorted(options, key=lambda o: (-o["feasible"], -o["score"])):
        if o["signature"] in seen:
            continue
        seen.add(o["signature"])
        uniq.append(o)
    uniq = uniq[:n_opts]
    for i, o in enumerate(uniq):
        o["id"] = i + 1
        o["name"] = f"Option {i + 1}"
    return {"options": uniq, "warnings": warnings, "goal": goal, "vastu_mode": vastu_mode, "project_type": ptype,
            "plot": _plot_dict(plot, land, sb), "regulatory": reg, "brief": brief}


def _front_side(plate: Rect, edge) -> str:
    nx, ny = edge.normal
    return max({"bottom": (0, -1), "top": (0, 1), "left": (-1, 0), "right": (1, 0)}.items(), key=lambda kv: kv[1][0] * nx + kv[1][1] * ny)[0]


def _plot_dict(plot, land, sb) -> dict:
    return {"polygon": _coords(plot.polygon), "north_deg": plot.north_deg, "area_sqft": round(plot.area, 1), "area_label": land.label(plot.area),
            "corner": plot.corner, "entry_edge": plot.entry_edge, "vehicle_edge": plot.vehicle_edge,
            "edges": [{"index": e.index, "a": [round(e.a[0], 3), round(e.a[1], 3)], "b": [round(e.b[0], 3), round(e.b[1], 3)], "length_ft": round(e.length, 2),
                       "compass": e.compass, "role": e.role, "road_width_m": e.road_width_m, "adjoining": e.adjoining,
                       "setback_ft": round(e.setback_ft, 3), "setback_label": U.length_label(e.setback_ft), "setback_source": e.setback_source} for e in plot.edges],
            "envelope": _coords(sb["envelope"]), "envelope_area_sqft": round(sb["envelope"].area, 1)}


def _build_option(vi, var, ptype, plot, envelope, plate, front_side, floors, stilt, f2f, reg, prog, rooms_in, cfg, goal, weights,
                  vastu_mode, vastu_over, brief, land, zone, sb) -> dict | None:
    cfg = copy.deepcopy(cfg)
    planner = UnitPlanner(cfg, goal if goal in GOALS else "balanced", vastu_mode, var["seed"], room_overrides=rooms_in.get("overrides"),
                          options=rooms_in.get("options"))
    warnings: list[str] = []
    floors_out: list[dict] = []
    fps: list[tuple[FloorPlan, str, int, int, str]] = []
    n_flats = 0
    unit_types: list[str] = []
    if ptype == "house":
        gf = plan_house_floor(plate, "house", cfg, planner, front_side, plot.north_deg, plot.polygon.bounds, rank=var["rank"], label="Ground Floor")
        fps.append((gf, "Ground Floor", 0, 1, "house"))
        if floors > 1:
            st = next((r.cell for u in gf.units if u.layout for r in u.layout.rooms.values() if r.type == "stair"), None)
            uf = plan_house_floor(plate, "house_upper", cfg, planner, front_side, plot.north_deg, plot.polygon.bounds, rank=var["rank"], label="Upper Floor",
                                  stair_below=st or Rect(plate.x, plate.y, 1, 1))
            _stair_stack_check(gf, uf, warnings)
            fps.append((uf, "Upper Floor (typical)", 1, floors - 1, "house_upper"))
        n_flats = 1
    elif ptype in ("apartment", "mixed"):
        res_floors = floors - (1 if ptype == "mixed" else 0)
        unit_types, w2 = unit_mix_per_floor(prog, plate, cfg, reg, res_floors, goal)
        warnings += w2
        typ = plan_residential_floor(plate, unit_types, cfg, planner, front_side, plot.north_deg, plot.polygon.bounds, core_pos=var["core_pos"], rank=var["rank"])
        if ptype == "mixed":
            gf = plan_commercial_floor(plate, brief, cfg, front_side, plot.north_deg, plot.polygon.bounds, kind="shops")
            gf.core = typ.core
            fps.append((gf, "Ground Floor (shops)", 0, 1, "commercial"))
        elif stilt:
            fps.append((plan_stilt_floor(plate, typ, cfg), "Stilt (parking)", 0, 1, "stilt"))
        start = 1 if (ptype == "mixed" or stilt) else 0
        fps.append((typ, "Typical Floor", start, res_floors, "typical"))
        n_flats = len(unit_types) * res_floors
    elif ptype == "commercial":
        gf = plan_commercial_floor(plate, brief, cfg, front_side, plot.north_deg, plot.polygon.bounds, kind="shops")
        fps.append((gf, "Ground Floor (shops)", 0, 1, "commercial"))
        if floors > 1:
            of = plan_commercial_floor(plate, brief, cfg, front_side, plot.north_deg, plot.polygon.bounds, kind="office")
            of.core = gf.core
            fps.append((of, "Office Floor (typical)", 1, floors - 1, "office"))
    else:
        return None

    # one column grid for the whole building, derived from the main residential/ground floor
    primary = next((fp for fp, name, level, repeat, kind in fps if kind in ("typical", "house")), fps[0][0])
    grid = column_grid(primary.plate, floor_cells(primary), cfg, kind=get(cfg, "_column_kind", "house"))
    warnings += grid["warnings"]
    for fp, name, level, repeat, kind in fps:
        floors_out.append(floor_to_dict(fp, cfg, name, level, repeat, kind, grid=grid))
    feasible = all(u["error"] is None for f in floors_out for u in f["units"])

    # --- areas -------------------------------------------------------------------
    factor = float(get(cfg, "units.super_builtup_factor", 1.33))
    bal_exempt_depth = U.parse_length(get(cfg, "regulatory.far_exempt.balcony_max_depth_ft", 4))
    total_builtup = 0.0
    far_area = 0.0
    total_carpet = 0.0
    total_units_builtup = 0.0
    total_balcony = 0.0
    units_summary = []
    for f in floors_out:
        rep = f["repeat"]
        total_builtup += f["areas"]["builtup_sqft"] * rep
        exempt = 0.0
        if f["kind"] == "stilt" and get(cfg, "regulatory.far_exempt.stilt_parking", True):
            exempt = f["areas"]["builtup_sqft"]
        for u in f["units"]:
            for r in u["rooms"]:
                if r["type"] == "balcony" and min(r["cell"]["w"], r["cell"]["h"]) <= bal_exempt_depth + 0.01 and get(cfg, "regulatory.far_exempt.balcony_max_depth_ft"):
                    exempt += r["cell"]["w"] * r["cell"]["h"]
            total_carpet += u["carpet_sqft"] * rep
            total_units_builtup += u["builtup_sqft"] * rep
            total_balcony += u["balcony_sqft"] * rep
            units_summary.append({"floor": f["name"], "label": u["label"], "type": u["type"], "carpet_sqft": u["carpet_sqft"], "balcony_sqft": u["balcony_sqft"],
                                  "builtup_sqft": u["builtup_sqft"], "super_builtup_sqft": round(u["builtup_sqft"] * factor, 1), "count": rep,
                                  "vastu": u.get("scores", {}).get("vastu"), "score": u["score"], "error": u["error"]})
        far_area += (f["areas"]["builtup_sqft"] - exempt) * rep
    far_used = far_area / plot.area
    coverage = plate.area / plot.area * 100
    pk = get(cfg, "regulatory.parking")
    ecs_required = 0.0
    if ptype == "house":
        ecs_required = float(pk.get("ecs_per_house", 1))
    else:
        for t in unit_types:
            ecs_required += float(pk.get(f"ecs_per_{t}", 1.0))
        ecs_required *= (floors - (1 if ptype == "mixed" else 0)) if ptype != "commercial" else 0
        if ptype in ("mixed", "commercial"):
            comm_sqm = U.sqft_to_sqm(plate.area) * (1 if ptype == "mixed" else floors)
            ecs_required += comm_sqm / 100 * float(pk.get("ecs_per_100sqm_commercial", 2))
    height_ft = reg["building_height_ft"]

    # --- site plan -----------------------------------------------------------------
    sp = plan_site(plot, envelope, plate, cfg, {**brief.get("services_brief", {}), "project_type": ptype, "services": brief.get("services")},
                   max(n_flats, 1), ecs_required, stilt, reg["high_rise"], reg["lift_required"], vastu_mode)
    stilt_bays = sum(len(f["parking"]) for f in floors_out if f["kind"] == "stilt")
    ecs_provided = sp["ecs_provided"] + stilt_bays
    warnings += sp["warnings"]

    # --- vastu report ------------------------------------------------------------------
    rows: list[dict] = []
    items = []
    typ = floors_out[-1] if ptype != "house" else floors_out[0]
    if typ["core"].get("stair"):
        s = typ["core"]["stair"]; items.append({"rule": "stair", "label": "Staircase (core)", "x": s["x"] + s["w"] / 2, "y": s["y"] + s["h"] / 2})
    ge = sp["pedestrian_entry"]
    items.append({"rule": "main_entry", "label": "Main entrance (plot)", "x": ge["x"], "y": ge["y"]})
    for sv in sp["services"]:
        if sv.get("placed") and sv.get("rule"):
            r = sv["rect"]
            items.append({"rule": sv["rule"], "label": sv["label"], "x": r.x + r.w / 2, "y": r.y + r.h / 2})
    rows += V.building_rows(items, plot.polygon.bounds, plot.north_deg, cfg, vastu_mode, vastu_over)
    mr = V.mass_row((plate.cx, plate.cy), (plot.polygon.centroid.x, plot.polygon.centroid.y), plot.north_deg, cfg, vastu_mode, vastu_over)
    if mr:
        rows.append(mr)
    unit_vastu = {}
    for (fp, name, level, repeat, kind), f in zip(fps, floors_out):
        for up in fp.units:
            if up.layout:
                ur = V.unit_rows(up.layout, cfg, vastu_mode, vastu_over, unit_label=f"{up.label}" if ptype != "house" else name)
                rows += ur
                unit_vastu[f"{name}/{up.label}"] = V.summarise(ur)
    vsum = V.summarise(rows)
    if vsum["strict_failures"]:
        warnings.append("Strict Vastu rules not met (hard constraints take precedence): " + "; ".join(vsum["strict_failures"]))

    # --- option score ---------------------------------------------------------------------
    unit_scores = [u["scores"] for f in floors_out for u in f["units"] if u.get("scores")]
    def mean(key):
        vals = [s.get(key, 0) for s in unit_scores]
        return sum(vals) / len(vals) if vals else 0.0
    eff = total_carpet / (total_units_builtup * factor) if total_units_builtup else 0.0
    target = float(get(cfg, "scoring.efficiency_target", 0.75))
    sub = {"carpet_efficiency": min(1.0, eff / target), "vastu": (vsum["score"] or 0) / 100, "room_quality": mean("room_quality"),
           "light": mean("light"), "circulation": mean("circulation"), "structure": sum(f["columns"]["regularity"] for f in floors_out) / max(len(floors_out), 1)}
    score = round(100 * sum(weights.get(k, 0) * v for k, v in sub.items()) / max(sum(weights.values()), 1), 1)
    if not feasible:
        score = round(score * 0.5, 1)

    schedule = merge_schedules([{**u["schedule"], "doors": {k: {**v, "count": v["count"] * f["repeat"]} for k, v in u["schedule"].get("doors", {}).items()},
                                 "windows": {k: {**v, "count": v["count"] * f["repeat"]} for k, v in u["schedule"].get("windows", {}).items()}}
                                for f in floors_out for u in f["units"] if u.get("schedule")])
    all_warnings = list(dict.fromkeys(warnings + [w for f in floors_out for w in f["warnings"]] + [w for f in floors_out for u in f["units"] for w in u["warnings"]]
                                      + [f"{u['label']}: {r}" for f in floors_out for u in f["units"] for r in u["relaxed"]]))
    summary = _summary_line(ptype, eff, vsum["score"], n_flats, unit_types, feasible, floors_out)
    sig = "|".join(f"{u['label']}:{','.join(r['key'] + str(round(r['cell']['x'])) + ',' + str(round(r['cell']['y'])) for r in u['rooms'])}" for f in floors_out for u in f["units"])
    return {
        "id": vi + 1, "name": f"Option {vi + 1}", "goal": goal, "variant": var, "summary": summary, "score": score, "scores": {k: round(v, 3) for k, v in sub.items()},
        "feasible": feasible, "signature": sig,
        "plot": _plot_dict(plot, land, sb), "footprint": _rect(plate), "front_side": front_side,
        "regulatory": {**reg, "far_used": round(far_used, 3), "coverage_pct": round(coverage, 1), "floors": floors, "stilt": stilt,
                       "height_label": U.length_label(height_ft), "far_ok": far_used <= reg["far_permitted"] + 1e-6,
                       "coverage_ok": coverage <= reg["coverage_pct_max"] + 1e-6, "height_ok": height_ft <= reg["max_height_ft"] + 1e-6},
        "floors": floors_out,
        "site": {"gate": sp["gate"], "pedestrian_entry": sp["pedestrian_entry"], "driveway": [[round(x, 3), round(y, 3)] for x, y in sp["driveway"]],
                 "parking": [_rect(b) for b in sp["parking"]], "services": [{**{k: v for k, v in s.items() if k != "rect"}, "rect": _rect(s.get("rect"))} for s in sp["services"]],
                 "open_area_sqft": round(sp["open_area_sqft"], 1)},
        "areas": {"plot_sqft": round(plot.area, 1), "plot_label": land.label(plot.area), "footprint_sqft": round(plate.area, 1), "total_builtup_sqft": round(total_builtup, 1),
                  "far_area_sqft": round(far_area, 1), "total_carpet_sqft": round(total_carpet, 1), "total_balcony_sqft": round(total_balcony, 1),
                  "total_super_builtup_sqft": round(total_units_builtup * factor, 1), "common_sqft": round(total_builtup - total_units_builtup, 1),
                  "efficiency": round(eff, 3), "super_builtup_factor": factor, "units": units_summary, "n_flats": n_flats,
                  "parking": {"ecs_required": round(ecs_required, 1), "ecs_provided": ecs_provided, "surface_bays": sp["ecs_provided"], "stilt_bays": stilt_bays}},
        "schedule": schedule,
        "vastu": {"rows": rows, "summary": vsum, "units": unit_vastu, "mode": vastu_mode},
        "columns_csv": "\n".join(f"# {f['name']}\n{f['columns_csv']}" for f in floors_out),
        "warnings": all_warnings,
    }


def _stair_stack_check(gf: FloorPlan, uf: FloorPlan, warnings: list) -> None:
    def stair(fp):
        for u in fp.units:
            if u.layout:
                for r in u.layout.rooms.values():
                    if r.type == "stair":
                        return r.cell
        return None
    a, b = stair(gf), stair(uf)
    if a and b:
        from .geometry import overlap_area
        ov = overlap_area(a, b) / min(a.area, b.area)
        if ov < 0.6:
            warnings.append(f"Upper-floor staircase does not stack over the ground-floor stair ({ov*100:.0f}% overlap); align them before structural design.")


def _summary_line(ptype, eff, vscore, n_flats, unit_types, feasible, floors_out) -> str:
    parts = []
    if ptype != "house" and ptype != "commercial":
        from collections import Counter
        c = Counter(unit_types)
        parts.append(f"{len(unit_types)} flats/floor (" + ", ".join(f"{v}x{k.upper()}" for k, v in sorted(c.items())) + ")")
    parts.append(f"carpet efficiency {eff*100:.0f}%")
    if vscore is not None:
        parts.append(f"Vastu {vscore:.0f}%")
    relaxed = sum(1 for f in floors_out for u in f["units"] if u.get("relaxed"))
    if relaxed:
        parts.append(f"{relaxed} unit(s) with relaxed room minimums")
    if not feasible:
        parts.append("some units could not be planned")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Live editing: re-score a unit whose tree was edited (wall drag / room swap)
# ---------------------------------------------------------------------------
def rescore_unit(option: dict, floor_index: int, unit_label: str, tree: dict, brief: dict) -> dict:
    cfg = load_config(overrides=brief.get("config"))
    project = brief.get("project", {}) or {}
    goal, weights = resolve_goal(project, cfg)
    vastu_mode = str(project.get("vastu", get(cfg, "vastu.strictness_default", "preferred"))).lower()
    f = option["floors"][floor_index]
    u = next(x for x in f["units"] if x["label"] == unit_label)
    rooms_in = brief.get("rooms", {}) or {}
    specs = build_programme(u["type"], cfg, overrides=rooms_in.get("overrides"), options=rooms_in.get("options"))
    spec_map = {s.key: s for s in specs}
    corridor = U.parse_length(get(cfg, "regulatory.circulation.min_corridor_ft", 4.0))
    spec_map["passage"] = RoomSpec("passage", "passage", "Passage", corridor, corridor, 45.0, circulation=True)
    for r in u["rooms"]:
        if r["key"] not in spec_map:
            base = get(cfg, f"rooms.defaults.{r['type']}") or {}
            mn = base.get("min", [4, 4])
            spec_map[r["key"]] = RoomSpec(r["key"], r["type"], r["label"], min(mn), max(mn), float(base.get("target", 40)), bool(base.get("habitable")),
                                          base.get("external_wall"), circulation=bool(base.get("circulation")))
    node = Node.from_dict(tree, spec_map)
    c = u["clear"]
    ctx = UnitContext(Rect(c["x"], c["y"], c["w"], c["h"]), u["external"], entry_side=u["entry_side"], north_deg=option["plot"]["north_deg"], label=unit_label,
                      zone_bounds=tuple(u["zone_bounds"]) if u.get("zone_bounds") else None, entry_via_stair=bool(u.get("entry_via_stair")))
    lay = realise(node, ctx, cfg)
    if lay is None:
        return {"ok": False, "error": "The edited walls leave a room below its minimum size.", "unit": u}
    check_hard(lay, cfg)
    score_layout(lay, cfg, goal if goal in GOALS else "balanced", vastu_mode)
    up = UnitPlan(unit_label, u["type"], Rect(u["rect"]["x"], u["rect"]["y"], u["rect"]["w"], u["rect"]["h"]), ctx, layout=lay)
    cols = [(c["x"], c["y"], c["w"], c["d"]) for c in f["columns"]["columns"]]
    up.openings = place_openings(lay, cfg, main_door=True, columns=cols)
    d = unit_to_dict(up, cfg)
    d["hard_ok"] = lay.hard_ok
    return {"ok": True, "unit": d, "violations": lay.violations}


def _bounds(poly_coords):
    xs = [p[0] for p in poly_coords]; ys = [p[1] for p in poly_coords]
    return (min(xs), min(ys), max(xs), max(ys))
