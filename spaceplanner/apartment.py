"""Floor-plate planning: core (lift + stair + lobby), corridor, unit division, shafts.

Typology used (fits most Bihar plots):
  * 1-2 units per floor: single row  [unit | CORE | unit], entry from the core lobby.
  * 3+ units per floor: two rows either side of a full-width corridor; the core is a
    slim block between the two units of one row (rear by default, front if that is the
    better Vastu zone under the VASTU goal).
Typical floors repeat, so columns, shafts and wet areas stack by construction.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field

from . import units as U
from .config import get
from .geometry import Rect
from .layout import UnitContext, Layout, Node, RoomSpec, build_programme, generate, realise, check_hard, score_layout, SIDES
from .openings import place_openings
from .site import zone_grid

OPP = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}


@dataclass
class UnitPlan:
    label: str
    unit_type: str
    rect: Rect                      # outer rect within the plate (to wall centre / external face)
    ctx: UnitContext
    layout: Layout | None = None
    openings: dict = field(default_factory=dict)
    error: str | None = None
    mirror_of: str | None = None
    specs: list = field(default_factory=list)


@dataclass
class FloorPlan:
    kind: str
    plate: Rect
    units: list[UnitPlan] = field(default_factory=list)
    core: dict = field(default_factory=dict)          # stair, lifts, lobby rects
    corridor: Rect | None = None
    shafts: list[dict] = field(default_factory=list)
    extra_rooms: list[dict] = field(default_factory=list)   # e.g. shops, toilets, entrance lobby
    parking: list[Rect] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def wall_thicknesses(cfg: dict) -> tuple[float, float]:
    mat = get(cfg, "construction.wall_material", "brick")
    return (U.parse_length(get(cfg, f"construction.external_wall_in.{mat}", 9), "in"),
            U.parse_length(get(cfg, f"construction.internal_wall_in.{mat}", 4.5), "in"))


def unit_target_carpet(unit_type: str, cfg: dict) -> float:
    rng_ = get(cfg, f"rooms.unit_programmes.{unit_type}.carpet") or [800, 1000]
    return (rng_[0] + rng_[1]) / 2


def core_block(cfg: dict, n_lifts: int, row_depth: float, need_lift: bool) -> dict:
    """Size a slim core block: stair + lobby + lift(s). Returns dict with w, d and sub-rects relative to (0,0)."""
    sw, sl = get(cfg, "services.stair_apartment_ft", [9, 16])
    lobby_d = float(get(cfg, "regulatory.circulation.lift_lobby_depth_ft", 8))
    sizes = get(cfg, "regulatory.lift.car_sizes_mm", {})
    car = sizes.get(8) or list(sizes.values())[0]
    clr = float(get(cfg, "regulatory.lift.shaft_clearance_mm", 200))
    lw = U.parse_length(car[0] + 2 * clr, "mm") + 0.75
    ld = U.parse_length(car[1] + 2 * clr, "mm") + 0.75
    lifts = n_lifts if need_lift else 0
    # column arrangement: stair, then lobby, then lifts stacked along the depth
    col_d = sl + lobby_d + lifts * ld
    col_w = max(sw, lw) + 0.75
    if col_d <= row_depth or lifts == 0:
        parts = {"stair": Rect(0, 0, sw, sl), "lobby": Rect(0, sl, col_w, lobby_d)}
        y = sl + lobby_d
        for i in range(lifts):
            parts[f"lift{i+1}"] = Rect(0, y, lw, ld)
            y += ld
        return {"w": col_w, "d": min(max(col_d, row_depth if lifts == 0 else col_d), row_depth) if row_depth >= col_d else col_d, "parts": parts, "arrangement": "column"}
    # side-by-side: stair | lifts, lobby in front
    w = sw + lifts * lw + 0.75
    d = max(sl, ld) + lobby_d
    parts = {"stair": Rect(0, lobby_d, sw, sl), "lobby": Rect(0, 0, w, lobby_d)}
    for i in range(lifts):
        parts[f"lift{i+1}"] = Rect(sw + 0.75 + i * lw, lobby_d, lw, ld)
    return {"w": w, "d": d, "parts": parts, "arrangement": "side"}


def mirror_tree_x(node: Node) -> Node:
    if node.kind == "leaf":
        return Node("leaf", spec=node.spec, rot=node.rot)
    kids = [mirror_tree_x(c) for c in node.children]
    n = Node(node.kind, kids[::-1] if node.kind == "V" else kids)
    n.ratio = node.ratio[::-1] if (node.ratio and node.kind == "V") else node.ratio
    return n


def mirror_tree_y(node: Node) -> Node:
    if node.kind == "leaf":
        return Node("leaf", spec=node.spec, rot=node.rot)
    kids = [mirror_tree_y(c) for c in node.children]
    n = Node(node.kind, kids[::-1] if node.kind == "H" else kids)
    n.ratio = node.ratio[::-1] if (node.ratio and node.kind == "H") else node.ratio
    return n


class UnitPlanner:
    """Plans units with caching + mirroring so symmetric flats reuse one search."""

    def __init__(self, cfg: dict, goal: str, vastu_mode: str, seed: int, room_overrides: dict | None = None, options: dict | None = None):
        self.cfg, self.goal, self.vastu_mode, self.seed = cfg, goal, vastu_mode, seed
        self.room_overrides = room_overrides or {}
        self.options = options or {}
        self.cache: dict[tuple, Layout] = {}

    def key(self, unit_type, ctx: UnitContext, mirror: str | None):
        ext = dict(ctx.external)
        entry = ctx.entry_side
        if mirror == "x":
            ext = {"left": ext.get("right"), "right": ext.get("left"), "top": ext.get("top"), "bottom": ext.get("bottom")}
            entry = OPP[entry] if entry in ("left", "right") else entry
        if mirror == "y":
            ext = {"top": ext.get("bottom"), "bottom": ext.get("top"), "left": ext.get("left"), "right": ext.get("right")}
            entry = OPP[entry] if entry in ("top", "bottom") else entry
        return (unit_type, round(ctx.rect.w, 1), round(ctx.rect.h, 1), tuple(sorted(k for k, v in ext.items() if v)), entry, round(ctx.north_deg, 1))

    def plan(self, unit: UnitPlan, rank: int = 0) -> None:
        specs = build_programme(unit.unit_type, self.cfg, overrides=self.room_overrides, options=self.options)
        unit.specs = specs
        for mirror in (None, "x", "y"):
            k = self.key(unit.unit_type, unit.ctx, mirror)
            if k in self.cache:
                src = self.cache[k]
                tree = src.tree if mirror is None else (mirror_tree_x(src.tree) if mirror == "x" else mirror_tree_y(src.tree))
                lay = realise(tree, unit.ctx, self.cfg)
                if lay is not None:
                    check_hard(lay, self.cfg)
                    if lay.hard_ok:
                        score_layout(lay, self.cfg, self.goal, self.vastu_mode)
                        lay.relaxed = list(src.relaxed)
                        unit.layout = lay
                        unit.mirror_of = f"mirror-{mirror}" if mirror else "same"
                        unit.openings = place_openings(lay, self.cfg, main_door=True)
                        return
        lays = generate(unit.ctx, specs, self.cfg, goal=self.goal, vastu_mode=self.vastu_mode, seed=self.seed, keep=max(1, rank + 1))
        if not lays:
            unit.error = "No feasible layout for this unit size; enlarge the unit or relax room minimums."
            return
        lay = lays[min(rank, len(lays) - 1)]
        self.cache[self.key(unit.unit_type, unit.ctx, None)] = lay
        unit.layout = lay
        unit.openings = place_openings(lay, self.cfg, main_door=True)


# ---------------------------------------------------------------------------
# residential floor
# ---------------------------------------------------------------------------
def plan_residential_floor(plate: Rect, unit_types: list[str], cfg: dict, planner: UnitPlanner, front_side: str,
                           north_deg: float, plot_bounds, core_pos: str = "auto", rank: int = 0) -> FloorPlan:
    t_ext, t_int = wall_thicknesses(cfg)
    fp = FloorPlan("typical", plate)
    n = len(unit_types)
    if n == 0:
        fp.warnings.append("No units requested on this floor.")
        return fp
    along_x = front_side in ("bottom", "top")
    # work in a canonical frame: front = bottom. We transform rects back at the end.
    W, D = (plate.w, plate.h) if along_x else (plate.h, plate.w)
    corr_d = U.parse_length(get(cfg, "regulatory.circulation.apartment_corridor_ft", 5))
    lift_needed = get(cfg, "_lift_required", True)
    n_lifts = int(get(cfg, "_lifts_per_core", 1))
    zone = zone_grid(plot_bounds, north_deg)

    rows: list[list[str]]
    if n <= 2:
        rows = [list(unit_types)]
    else:
        k = math.ceil(n / 2)
        rows = [list(unit_types[:k]), list(unit_types[k:])]
    two_rows = len(rows) == 2
    # row depths
    if two_rows:
        t0 = sum(unit_target_carpet(t, cfg) for t in rows[0])
        t1 = sum(unit_target_carpet(t, cfg) for t in rows[1])
        usable = D - corr_d
        d0 = usable * t0 / (t0 + t1)
        d1 = usable - d0
        # keep both rows at least 24 ft deep
        d0 = min(max(d0, 24.0), usable - 24.0) if usable > 48 else usable / 2
        d1 = usable - d0
        row_rects = [Rect(0, 0, W, d0), Rect(0, d0 + corr_d, W, d1)]
        corridor = Rect(0, d0, W, corr_d)
    else:
        row_rects = [Rect(0, 0, W, D)]
        corridor = None

    # core row selection
    core_row = len(rows) - 1
    if two_rows and core_pos == "front":
        core_row = 0
    elif two_rows and core_pos == "auto" and planner.goal == "vastu":
        # pick the row whose centre is the better stair zone
        good = get(cfg, "vastu.rules.stair.good", ["S", "W", "SW"])
        best = None
        for i, rr in enumerate(row_rects):
            cx, cy = W / 2, rr.cy
            px, py = _to_plate(cx, cy, plate, front_side)
            z = zone(px, py)
            sc = 2 if z in good else 1 if z in get(cfg, "vastu.rules.stair.alt", []) else 0
            if best is None or sc > best[0]:
                best = (sc, i)
        core_row = best[1]
    core = core_block(cfg, n_lifts, row_rects[core_row].h, lift_needed)
    core_w = core["w"]
    core_rect = None

    unit_rects: list[tuple[str, Rect, str, int]] = []   # (type, rect, entry_side, row)
    for ri, (row, rr) in enumerate(zip(rows, row_rects)):
        items = [("unit", t) for t in row]
        if ri == core_row:
            items.insert(len(items) // 2 if len(items) > 1 else 1, ("core", None))
        widths = []
        for kind, t in items:
            widths.append(core_w if kind == "core" else unit_target_carpet(t, cfg))
        unit_total = sum(w for (k, _), w in zip(items, widths) if k == "unit")
        avail = W - (core_w if ri == core_row else 0)
        x = 0.0
        for (kind, t), wgt in zip(items, widths):
            w = core_w if kind == "core" else avail * wgt / unit_total
            r = Rect(x, rr.y, w, rr.h)
            if kind == "core":
                core_rect = r
            else:
                # entry: from the corridor (two rows) or from the core side (single row)
                if two_rows:
                    entry = "top" if ri == 0 else "bottom"
                else:
                    entry = "right" if x + w <= (core_rect.x if core_rect else W) + 1e-6 and core_rect is None or (core_rect is None) else "left"
                    if core_rect is not None:
                        entry = "left"   # unit to the right of the core
                    elif any(k == "core" for k, _ in items[items.index((kind, t)) + 1:]):
                        entry = "right"
                unit_rects.append((t, r, entry, ri))
            x += w
    if core_rect is None:   # single row without core position resolved (n==1)
        core_rect = Rect(W - core_w, 0, core_w, row_rects[0].h)

    # core parts positioned inside the core rect (lobby towards the corridor / front)
    parts = {}
    lobby_first = (core_row == 0 and two_rows)   # corridor above the core row => lobby at top
    for name, pr in core["parts"].items():
        if two_rows and core_row == 1:
            y = core_rect.y + pr.y                      # stair first at the corridor side
        elif two_rows and core_row == 0:
            y = core_rect.y2 - pr.y - pr.h              # flip so the lobby faces the corridor
        else:
            y = core_rect.y + pr.y                      # single row: stair at the front, lobby behind
        parts[name] = Rect(core_rect.x + pr.x, y, pr.w, pr.h)
    # single-row: entrance lobby is the front strip of the core
    if not two_rows:
        parts["lobby"] = Rect(core_rect.x, core_rect.y, core_rect.w, float(get(cfg, "regulatory.circulation.lift_lobby_depth_ft", 8)))
        stair = core["parts"]["stair"]
        parts["stair"] = Rect(core_rect.x, core_rect.y + parts["lobby"].h, stair.w, stair.h)
        y = parts["stair"].y2
        for name, pr in core["parts"].items():
            if name.startswith("lift"):
                parts[name] = Rect(core_rect.x, y, pr.w, pr.h)
                y += pr.h

    # transform everything to the plate frame
    def tr(r: Rect) -> Rect:
        return _rect_to_plate(r, plate, front_side)

    fp.core = {name: tr(r) for name, r in parts.items()}
    fp.core["block"] = tr(core_rect)
    fp.corridor = tr(corridor) if corridor else None
    labels = "ABCDEFGH"
    for i, (t, r, entry, ri) in enumerate(unit_rects):
        pr = tr(r)
        entry_p = _side_to_plate(entry, front_side)
        external = {sd: _touches(pr, plate, sd) for sd in SIDES}
        # clear rect: external faces lose the full external wall, party walls half of it
        shrink = {sd: (t_ext if external[sd] else t_ext / 2) for sd in SIDES}
        clear = pr.shrink(left=shrink["left"], top=shrink["top"], right=shrink["right"], bottom=shrink["bottom"])
        ctx = UnitContext(clear, external, entry_side=entry_p, north_deg=north_deg, label=f"Flat {labels[i]}")
        up = UnitPlan(f"Flat {labels[i]}", t, pr, ctx)
        planner.plan(up, rank=rank)
        if up.error:
            fp.warnings.append(f"{up.label} ({t}, {U.ftin(clear.w)} x {U.ftin(clear.h)}): {up.error}")
        fp.units.append(up)
    fp.shafts = place_shafts(fp, cfg)
    if len(rows[0]) > 3 or (two_rows and len(rows[1]) > 3):
        fp.warnings.append("More than three flats in a row: middle flats get light from one side only.")
    return fp


def _touches(r: Rect, plate: Rect, side: str, tol: float = 0.05) -> bool:
    return {"left": abs(r.x - plate.x) < tol, "right": abs(r.x2 - plate.x2) < tol,
            "bottom": abs(r.y - plate.y) < tol, "top": abs(r.y2 - plate.y2) < tol}[side]


def _to_plate(x: float, y: float, plate: Rect, front: str) -> tuple[float, float]:
    # canonical frame: front at bottom, x along the front, y into the depth
    if front == "bottom":
        return (plate.x + x, plate.y + y)
    if front == "top":
        return (plate.x + x, plate.y2 - y)
    if front == "left":
        return (plate.x + y, plate.y + x)
    return (plate.x2 - y, plate.y + x)


def _rect_to_plate(r: Rect, plate: Rect, front: str) -> Rect:
    x1, y1 = _to_plate(r.x, r.y, plate, front)
    x2, y2 = _to_plate(r.x2, r.y2, plate, front)
    return Rect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))


def _side_to_plate(side: str, front: str) -> str:
    if front == "bottom":
        return side
    if front == "top":
        return OPP[side] if side in ("top", "bottom") else side
    m = {"left": {"bottom": "left", "top": "right", "left": "bottom", "right": "top"},
         "right": {"bottom": "right", "top": "left", "left": "bottom", "right": "top"}}
    return m[front][side]


def place_shafts(fp: FloorPlan, cfg: dict) -> list[dict]:
    """2' x 4' plumbing shafts against wet rooms: on external faces as projections, else in the corridor if wide enough."""
    sw, sd = get(cfg, "services.plumbing_shaft_ft", [2, 4])
    shafts = []
    corr = fp.corridor
    for up in fp.units:
        if not up.layout:
            continue
        for r in up.layout.rooms.values():
            if r.type not in ("toilet", "kitchen"):
                continue
            placed = False
            for side in r.external_sides:
                if side not in ("top", "bottom", "left", "right"):
                    continue
                if not up.ctx.external.get(side):
                    continue
                c = r.cell
                if side == "top":
                    rect = Rect(c.cx - sd / 2, up.rect.y2, sd, sw)
                elif side == "bottom":
                    rect = Rect(c.cx - sd / 2, up.rect.y - sw, sd, sw)
                elif side == "left":
                    rect = Rect(up.rect.x - sw, c.cy - sd / 2, sw, sd)
                else:
                    rect = Rect(up.rect.x2, c.cy - sd / 2, sw, sd)
                shafts.append({"rect": rect, "serves": f"{up.label} {r.spec.label}", "kind": "external duct"})
                placed = True
                break
            if not placed and corr is not None and corr.h >= 6.5 and corr.w >= 6.5:
                c = r.cell
                # toilet touching the corridor side?
                if abs(c.y2 - corr.y) < 0.8 or abs(c.y - corr.y2) < 0.8 or abs(c.x2 - corr.x) < 0.8 or abs(c.x - corr.x2) < 0.8:
                    if corr.w > corr.h:
                        y = corr.y if abs(c.y2 - corr.y) < 0.8 else corr.y2 - sw
                        rect = Rect(c.cx - sd / 2, y, sd, sw)
                    else:
                        x = corr.x if abs(c.x2 - corr.x) < 0.8 else corr.x2 - sw
                        rect = Rect(x, c.cy - sd / 2, sw, sd)
                    shafts.append({"rect": rect, "serves": f"{up.label} {r.spec.label}", "kind": "corridor duct"})
                    placed = True
            if not placed:
                fp.warnings.append(f"{up.label} {r.spec.label}: no shaft position on an outer wall; needs a sunken slab / internal duct.")
    return shafts


# ---------------------------------------------------------------------------
# house floors
# ---------------------------------------------------------------------------
def plan_house_floor(plate: Rect, programme: str, cfg: dict, planner: UnitPlanner, front_side: str, north_deg: float,
                     plot_bounds, rank: int = 0, label: str = "House", stair_below: Rect | None = None) -> FloorPlan:
    t_ext, _ = wall_thicknesses(cfg)
    fp = FloorPlan("house", plate)
    clear = plate.shrink(t_ext, t_ext, t_ext, t_ext)
    ctx = UnitContext(clear, {sd: True for sd in SIDES}, entry_side=front_side, north_deg=north_deg, label=label, zone_bounds=plot_bounds,
                      entry_via_stair=stair_below is not None)
    up = UnitPlan(label, programme, plate, ctx)
    if stair_below is not None:
        # upper floor: pick the candidate whose stair stacks best over the floor below
        from .geometry import overlap_area
        specs = build_programme(programme, cfg, overrides=planner.room_overrides, options=None)   # options apply to the main floor only
        up.specs = specs
        lays = generate(ctx, specs, cfg, goal=planner.goal, vastu_mode=planner.vastu_mode, seed=planner.seed, keep=40)
        if not lays:
            up.error = "No feasible layout for this unit size; enlarge the unit or relax room minimums."
        else:
            def stack(l):
                st = next((r.cell for r in l.rooms.values() if r.type == "stair"), None)
                return overlap_area(st, stair_below) / stair_below.area if st else 0.0
            lays.sort(key=lambda l: (-round(stack(l), 1), -l.total))
            up.layout = lays[min(rank, len(lays) - 1)]
            up.openings = place_openings(up.layout, cfg, main_door=True)
    else:
        planner.plan(up, rank=rank)
    if up.error:
        fp.warnings.append(f"{label}: {up.error}")
    fp.units.append(up)
    return fp


# ---------------------------------------------------------------------------
# commercial floors
# ---------------------------------------------------------------------------
def plan_commercial_floor(plate: Rect, brief: dict, cfg: dict, front_side: str, north_deg: float, plot_bounds, kind: str = "shops") -> FloorPlan:
    """Shops along the road frontage with a rear service band (core + toilets + store)."""
    t_ext, t_int = wall_thicknesses(cfg)
    fp = FloorPlan("commercial", plate)
    along_x = front_side in ("bottom", "top")
    W, D = (plate.w, plate.h) if along_x else (plate.h, plate.w)
    comm = brief.get("commercial", {}) or {}
    frontage = U.parse_length(comm.get("shop_frontage", 12))
    n_shops = int(comm.get("shop_count") or max(1, int(W // frontage)))
    shop_depth = U.parse_length(comm.get("shop_depth", min(D, 30)))
    core = core_block(cfg, int(get(cfg, "_lifts_per_core", 1)), D - shop_depth, get(cfg, "_lift_required", True))
    if kind == "shops":
        shop_w = W / n_shops
        for i in range(n_shops):
            r = Rect(i * shop_w, 0, shop_w, shop_depth)
            fp.extra_rooms.append({"label": f"Shop {i+1}", "type": "shop", "rect": _rect_to_plate(r, plate, front_side)})
        rear = Rect(0, shop_depth, W, D - shop_depth)
        if rear.h >= 8:
            core_r = Rect(rear.x + (rear.w - core["w"]) / 2, rear.y, core["w"], min(rear.h, core["d"]))
            fp.core = {"block": _rect_to_plate(core_r, plate, front_side)}
            for name, pr in core["parts"].items():
                fp.core[name] = _rect_to_plate(Rect(core_r.x + pr.x, core_r.y + pr.y, pr.w, min(pr.h, rear.h)), plate, front_side)
            tw = 8.0
            fp.extra_rooms.append({"label": "Toilets (M/F)", "type": "toilet", "rect": _rect_to_plate(Rect(rear.x, rear.y, tw, rear.h), plate, front_side)})
            fp.extra_rooms.append({"label": "Store / Services", "type": "store", "rect": _rect_to_plate(Rect(rear.x2 - tw, rear.y, tw, rear.h), plate, front_side)})
            fp.corridor = _rect_to_plate(Rect(rear.x + tw, rear.y, rear.w - 2 * tw, min(4.0, rear.h)), plate, front_side)
        else:
            fp.warnings.append("Plate too shallow for a rear service band; core placed inside the shop row.")
            core_r = Rect(W - core["w"], 0, core["w"], min(D, core["d"]))
            fp.core = {"block": _rect_to_plate(core_r, plate, front_side)}
    else:  # office floor: open plate + core + toilets at the rear
        rear_d = min(16.0, D / 3)
        rear = Rect(0, D - rear_d, W, rear_d)
        core_r = Rect(rear.x + (rear.w - core["w"]) / 2, rear.y, core["w"], rear.h)
        fp.core = {"block": _rect_to_plate(core_r, plate, front_side)}
        for name, pr in core["parts"].items():
            fp.core[name] = _rect_to_plate(Rect(core_r.x + pr.x, core_r.y + pr.y, pr.w, min(pr.h, rear.h)), plate, front_side)
        fp.extra_rooms.append({"label": "Office", "type": "office", "rect": _rect_to_plate(Rect(0, 0, W, D - rear_d), plate, front_side)})
        fp.extra_rooms.append({"label": "Toilets (M/F)", "type": "toilet", "rect": _rect_to_plate(Rect(0, rear.y, core_r.x, rear.h), plate, front_side)})
        fp.extra_rooms.append({"label": "Pantry / Store", "type": "store", "rect": _rect_to_plate(Rect(core_r.x2, rear.y, W - core_r.x2, rear.h), plate, front_side)})
    return fp


def plan_stilt_floor(plate: Rect, typical: FloorPlan, cfg: dict) -> FloorPlan:
    fp = FloorPlan("stilt", plate)
    fp.core = dict(typical.core)
    pk = get(cfg, "regulatory.parking")
    bay_w, bay_l = U.m_to_ft(pk["car_bay_m"][0]), U.m_to_ft(pk["car_bay_m"][1])
    aisle = U.m_to_ft(pk["driveway_width_m"])
    core = typical.core.get("block")
    bays = []
    if plate.w >= plate.h:
        rows_y = [plate.y + 0.75, plate.y2 - 0.75 - bay_l]
        for y in rows_y:
            x = plate.x + 0.75
            while x + bay_w <= plate.x2 - 0.75:
                r = Rect(x, y, bay_w, bay_l)
                if not (core and r.intersects(core)):
                    bays.append(r)
                x += bay_w
        if plate.h < 2 * bay_l + aisle:
            fp.warnings.append("Stilt depth is too small for two parking rows plus an aisle; one row shown.")
            bays = [b for b in bays if b.y == rows_y[0]]
    else:
        cols_x = [plate.x + 0.75, plate.x2 - 0.75 - bay_l]
        for x in cols_x:
            y = plate.y + 0.75
            while y + bay_w <= plate.y2 - 0.75:
                r = Rect(x, y, bay_l, bay_w)
                if not (core and r.intersects(core)):
                    bays.append(r)
                y += bay_w
    fp.parking = bays
    return fp
