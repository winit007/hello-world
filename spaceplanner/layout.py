"""Unit-level layout engine: slicing-tree generation, constraint checking and scoring.

A *unit* is one flat or one house floor: a rectangle (clear inside the external
walls) with some sides external. Rooms tile the rectangle as cells; internal
walls sit on cell boundaries. Candidates are random slicing trees biased by
architectural clusters (bedroom + attached toilet, kitchen + utility), filtered
by hard constraints and ranked by a weighted score.
"""
from __future__ import annotations
import math
import random
from dataclasses import dataclass, field

from . import units as U
from .config import get
from .geometry import Rect, shared_wall, OPPOSITE, EPS
from .site import zone_grid

SIDES = ["top", "right", "bottom", "left"]
CIRCULATION = {"living", "dining", "foyer", "passage"}
DOOR_HOST_ALLOWED = {
    "toilet": {"foyer", "passage", "living", "master", "bedroom", "guest", "servant", "dress"},
    "kitchen": {"dining", "living", "foyer", "passage"},
    "utility": {"kitchen"},
    "dress": {"master", "bedroom"},
    "balcony": {"master", "living", "bedroom", "dining"},
    "store": {"kitchen", "passage", "foyer", "living", "dining"},
    "pooja": {"living", "foyer", "passage", "dining"},
}


# ---------------------------------------------------------------------------
# Programme
# ---------------------------------------------------------------------------
@dataclass
class RoomSpec:
    key: str
    type: str
    label: str
    min_short: float
    min_long: float
    target: float
    habitable: bool = False
    external: str | None = None          # required | preferred | None
    attach_to: str | None = None         # key of host room (attached toilet, utility, balcony)
    circulation: bool = False
    wet: bool = False
    balcony: bool = False


def build_programme(unit_type: str, cfg: dict, overrides: dict | None = None, extra: list | None = None,
                    remove: list | None = None, options: dict | None = None) -> list[RoomSpec]:
    """Expand a unit type into RoomSpecs. ``overrides`` maps room type -> {min:[w,d], target:..}."""
    defaults = get(cfg, "rooms.defaults")
    prog = get(cfg, f"rooms.unit_programmes.{unit_type}")
    if prog is None:
        raise ValueError(f"unknown unit type {unit_type}")
    types = list(prog["rooms"])
    options = options or {}
    for t in (extra or []):
        types.append(t)
    for t in (remove or []):
        if t in types:
            types.remove(t)
    for flag, t in (("pooja", "pooja"), ("study", "study"), ("guest", "guest"), ("servant", "servant"), ("store", "store"), ("dress", "dress")):
        if options.get(flag) is True and t not in types:
            types.append(t)
        if options.get(flag) is False and t in types:
            types.remove(t)
    specs: list[RoomSpec] = []
    counts: dict[str, int] = {}
    last_bedroom_key: str | None = None
    for t in types:
        d = dict(defaults[t])
        if overrides and t in overrides:
            d.update(overrides[t])
        counts[t] = counts.get(t, 0) + 1
        key = t if counts[t] == 1 else f"{t}{counts[t]}"
        mn = d["min"]
        mn = [U.parse_length(mn[0]), U.parse_length(mn[1])]
        spec = RoomSpec(key=key, type=t, label=d["label"] + (f" {counts[t]}" if counts[t] > 1 and t not in ("toilet",) else ""),
                        min_short=min(mn), min_long=max(mn), target=float(d.get("target", mn[0] * mn[1])),
                        habitable=bool(d.get("habitable")), external=d.get("external_wall"),
                        circulation=bool(d.get("circulation")), wet=bool(d.get("wet")), balcony=bool(d.get("balcony")))
        if t in ("master", "bedroom", "guest"):
            last_bedroom_key = key
        if t == "toilet":
            # toilet directly after a bedroom is attached to it
            prev = specs[-1].type if specs else None
            if prev in ("master", "bedroom", "guest") and last_bedroom_key:
                spec.attach_to = last_bedroom_key
                spec.label = "Toilet (att.)"
            else:
                spec.label = "Toilet (common)"
        if t == "utility":
            spec.attach_to = "kitchen"
        if t == "dress":
            spec.attach_to = "master"
        if t == "balcony":
            spec.attach_to = None  # decided by layout: master or living
        specs.append(spec)
    # rename toilets for readability
    n = 0
    for s in specs:
        if s.type == "toilet":
            n += 1
            s.label = f"Toilet {n}" + (" (att.)" if s.attach_to else " (common)")
    return specs


# ---------------------------------------------------------------------------
# Slicing tree
# ---------------------------------------------------------------------------
class Node:
    __slots__ = ("kind", "children", "spec", "rect", "ratio", "rot")

    def __init__(self, kind: str, children=None, spec: RoomSpec | None = None, rot: bool = False):
        self.kind = kind            # 'leaf' | 'V' (children left->right) | 'H' (children bottom->top)
        self.children = children or []
        self.spec = spec
        self.rect: Rect | None = None
        self.ratio: list[float] | None = None   # explicit shares (editing) else by target areas
        self.rot = rot              # leaf only: True = long side runs along y

    def leaves(self) -> list["Node"]:
        if self.kind == "leaf":
            return [self]
        return [l for c in self.children for l in c.leaves()]

    def target(self) -> float:
        return self.spec.target if self.kind == "leaf" else sum(c.target() for c in self.children)

    def min_extent(self, wall: float) -> tuple[float, float]:
        """Conservative minimum (w, h) needed by this subtree."""
        if self.kind == "leaf":
            a, b = self.spec.min_long + wall, self.spec.min_short + wall
            return (b, a) if self.rot else (a, b)
        ex = [c.min_extent(wall) for c in self.children]
        if self.kind == "V":
            return (sum(e[0] for e in ex), max(e[1] for e in ex))
        return (max(e[0] for e in ex), sum(e[1] for e in ex))

    def to_dict(self) -> dict:
        if self.kind == "leaf":
            return {"kind": "leaf", "key": self.spec.key, "rot": self.rot}
        return {"kind": self.kind, "ratio": self.ratio, "children": [c.to_dict() for c in self.children]}

    @staticmethod
    def from_dict(d: dict, specs: dict[str, RoomSpec]) -> "Node":
        if d["kind"] == "leaf":
            return Node("leaf", spec=specs[d["key"]], rot=bool(d.get("rot")))
        n = Node(d["kind"], [Node.from_dict(c, specs) for c in d["children"]])
        n.ratio = d.get("ratio")
        return n


def _distribute(total: float, targets: list[float], mins: list[float]) -> list[float] | None:
    if sum(mins) > total + EPS:
        return None
    shares = [total * t / max(sum(targets), EPS) for t in targets]
    fixed: set[int] = set()
    for _ in range(len(shares) + 1):
        below = [i for i, s in enumerate(shares) if s < mins[i] - EPS and i not in fixed]
        if not below:
            break
        fixed.update(below)
        for i in fixed:
            shares[i] = mins[i]
        free = [i for i in range(len(shares)) if i not in fixed]
        remaining = total - sum(mins[i] for i in fixed)
        tsum = sum(targets[i] for i in free) or 1.0
        for i in free:
            shares[i] = remaining * targets[i] / tsum
    scale = total / sum(shares)
    return [s * scale for s in shares]


def allocate(node: Node, rect: Rect, wall: float) -> bool:
    node.rect = rect
    if node.kind == "leaf":
        return True
    mins = [c.min_extent(wall) for c in node.children]
    targets = node.ratio if node.ratio and len(node.ratio) == len(node.children) else [c.target() for c in node.children]
    if node.kind == "V":
        shares = _distribute(rect.w, targets, [m[0] for m in mins])
        if shares is None:
            return False
        x = rect.x
        for c, s in zip(node.children, shares):
            if not allocate(c, Rect(x, rect.y, s, rect.h), wall):
                return False
            x += s
    else:
        shares = _distribute(rect.h, targets, [m[1] for m in mins])
        if shares is None:
            return False
        y = rect.y
        for c, s in zip(node.children, shares):
            if not allocate(c, Rect(rect.x, y, rect.w, s), wall):
                return False
            y += s
    return True


def random_tree(specs: list[RoomSpec], rng: random.Random, entry_side: str, width: float | None = None,
                depth: float | None = None, wall: float = 0.375, external: dict | None = None,
                corridor: float = 4.0, stair_entry: bool = False) -> Node | None:
    """Random *band* layout.

    Rooms are packed into bands parallel to the entry side: entry band (foyer,
    living), middle bands (dining, kitchen, stair, small rooms) and a rear band
    (bedrooms with attached toilets). Leaves start in their *narrow* orientation
    (short side across the band) and are widened only while the band still fits,
    so feasibility is high. A full-width passage strip is inserted after the last
    band holding a circulation room whenever rooms lie beyond it, which guarantees
    access to the rear rooms. Rooms that need daylight are pushed to the external
    ends of internal bands. Returns None when the packing cannot fit.
    """
    along_x = entry_side in ("bottom", "top")
    ROW, COL = ("V", "H") if along_x else ("H", "V")
    external = external or {}
    W = width if width is not None else 1e9
    by_key = {s.key: s for s in specs}
    lateral = ("left", "right") if along_x else ("bottom", "top")
    lateral_ext = [sd for sd in lateral if external.get(sd)]
    far_side = {"bottom": "top", "top": "bottom", "left": "right", "right": "left"}[entry_side]
    outer_last = entry_side in ("bottom", "left")     # far side of the unit is the last COL child

    def leaf(sp: RoomSpec) -> Node:
        # narrow: short side across the band. For along_x bands "across" is x, so rot=True (long along y).
        return Node("leaf", spec=sp, rot=along_x)

    def needs_ext(node: Node) -> bool:
        return any(l.spec.external == "required" for l in node.leaves())

    def col(kids: list[Node], host_outer: bool) -> Node:
        """Stack kids across the band; kids[0] is the host. host_outer puts it on the far face."""
        kids = list(kids)
        if host_outer == outer_last:
            kids = kids[::-1]
        return Node(COL, kids)

    # --- clusters -----------------------------------------------------------
    clusters: list[dict] = []
    used: set[str] = set()
    beds = [b for b in specs if b.type in ("master", "bedroom", "guest")]
    att = {t.attach_to: t for t in specs if t.type == "toilet" and t.attach_to}
    if len([b for b in beds if b.key in att]) >= 2 and rng.random() < 0.6:
        b1, b2 = rng.sample([b for b in beds if b.key in att], 2)
        t1, t2 = att[b1.key], att[b2.key]
        clusters.append({"node": Node(ROW, [leaf(b1), Node(COL, [leaf(t1), leaf(t2)]), leaf(b2)]), "type": "bedrooms", "group": "rear"})
        used.update({b1.key, b2.key, t1.key, t2.key})
    for sp in specs:
        if sp.key in used or sp.type in ("balcony", "passage"):
            continue
        extras = [x for x in specs if x.attach_to == sp.key and x.key not in used]
        if extras and rng.random() < 0.92:
            kids = [leaf(sp)] + [leaf(x) for x in extras]
            if len(kids) > 2:
                kids = [kids[0], Node(COL, kids[1:])]
            if rng.random() < 0.5:
                rng.shuffle(kids)
                node = Node(ROW, kids)
            else:
                # host keeps the outer face (its external wall); living hugs the entry side; the
                # kitchen sits inside with its utility on the outer face (light through the utility)
                node = col(kids, host_outer=sp.type not in ("living", "foyer", "kitchen"))
            used.update(x.key for x in extras)
        elif sp.attach_to and sp.attach_to in by_key:
            continue
        else:
            node = leaf(sp)
        used.add(sp.key)
        group = "rear" if sp.type in ("master", "bedroom", "guest") else "entry" if sp.type in ("living", "foyer") else \
                "entry" if (sp.type == "stair" and stair_entry) else "middle" if sp.type in ("kitchen", "dining", "stair", "utility") else "any"
        clusters.append({"node": node, "type": sp.type, "group": group})
    for sp in specs:
        if sp.key in used:
            continue
        used.add(sp.key)
        if sp.type == "balcony":
            host_t = rng.choice(["master", "living", "master"])
            host = next((c for c in clusters if c["type"] == host_t), None) or \
                   next((c for c in clusters if c["type"] in ("bedrooms", "master", "living", "bedroom")), None)
            if host:
                host_t = host["type"]
                if rng.random() < 0.5:
                    host["node"] = Node(ROW, [host["node"], leaf(sp)] if rng.random() < 0.5 else [leaf(sp), host["node"]])
                else:
                    # balcony on the outer face, beyond its host (light comes through it)
                    host["node"] = col([leaf(sp), host["node"]], host_outer=host_t != "living")
                continue
        clusters.append({"node": leaf(sp), "type": sp.type, "group": "any"})
    small = [c for c in clusters if c["node"].kind == "leaf" and c["node"].spec.min_long <= 8
             and c["type"] not in ("living", "foyer", "passage", "kitchen")]
    rng.shuffle(small)
    while len(small) >= 2 and rng.random() < 0.6:
        a, b = small.pop(), small.pop()
        clusters.remove(a); clusters.remove(b)
        clusters.append({"node": Node(COL, [a["node"], b["node"]]), "type": a["type"], "group": "any"})

    def across(node: Node) -> float:
        ex = node.min_extent(wall)
        return ex[0] if along_x else ex[1]

    total = sum(across(c["node"]) for c in clusters)
    n_min = max(1, math.ceil(total / (W * 0.97)))
    if len(clusters) > 3:
        n_min = max(n_min, 2)
    n_bands = n_min + 1 if (len(clusters) > 3 and rng.random() < 0.35) else n_min
    n_bands = max(1, min(n_bands, len(clusters), 4))
    bands: list[list] = [[] for _ in range(n_bands)]
    widths = [0.0] * n_bands
    ext_slots = [len(lateral_ext)] * n_bands
    if external.get(entry_side):
        ext_slots[0] = 99
    if external.get(far_side):
        ext_slots[-1] = 99
    last = n_bands - 1

    def place(c, prefs: list[int]) -> bool:
        need = across(c["node"])
        ne = needs_ext(c["node"])
        ok = lambda i: widths[i] + need <= W + EPS and (not ne or ext_slots[i] > 0)
        cands = [i for i in prefs if ok(i)]
        if not cands and c["group"] != "entry":
            fallback = list(range(1, n_bands)) if c["group"] == "rear" else list(range(n_bands))
            cands = [i for i in fallback if ok(i)]
        if not cands:
            return False
        i = rng.choice(cands) if rng.random() < 0.5 else max(cands, key=lambda k: W - widths[k])
        bands[i].append(c)
        widths[i] += need
        if ne:
            ext_slots[i] -= 1
        return True

    order = clusters[:]
    rng.shuffle(order)
    order.sort(key=lambda c: ({"entry": 0, "rear": 1, "middle": 2, "any": 3}[c["group"]], -across(c["node"])))
    for c in order:
        g = c["group"]
        prefs = [0] if g == "entry" else [last] if g == "rear" and n_bands > 1 else \
                (list(range(1, last)) or [0]) if g == "middle" else list(range(n_bands))
        if not place(c, prefs):
            return None
    bands = [b for b in bands if b]
    widths = [sum(across(c["node"]) for c in b) for b in bands]

    # widen leaves while the band still fits (random order, so options differ)
    for bi, b in enumerate(bands):
        leaves = [l for c in b for l in c["node"].leaves()]
        rng.shuffle(leaves)
        for l in leaves:
            if l.spec.min_long == l.spec.min_short or rng.random() < 0.3:
                continue
            l.rot = not l.rot
            if sum(across(c["node"]) for c in b) > W + EPS:
                l.rot = not l.rot

    def order_band(b: list, band_idx: int) -> list[Node]:
        rng.shuffle(b)
        band_external = (band_idx == 0 and external.get(entry_side)) or (band_idx == len(bands) - 1 and external.get(far_side))
        if lateral_ext and len(lateral_ext) < 2 and not band_external:
            ne = [c for c in b if needs_ext(c["node"])]
            rest = [c for c in b if c not in ne]
            b = (rest + ne) if lateral_ext[0] in ("right", "top") else (ne + rest)
        kit = next((c for c in b if c["type"] == "kitchen"), None)
        din = next((c for c in b if c["type"] == "dining"), None)
        if kit and din and len(b) >= 3:
            b.remove(din)
            ki = b.index(kit)
            b.insert(ki + 1 if ki == 0 else ki, din)
        stair = next((c for c in b if c["type"] == "stair"), None)
        liv = next((c for c in b if c["type"] == "living"), None)
        if stair_entry and stair and liv and len(b) >= 3:
            b.remove(stair)
            li = b.index(liv)
            b.insert(li + 1 if li == 0 else li, stair)
        elif 0 < band_idx < len(bands) - 1 and len(b) >= 3:
            circ = [c for c in b if c["type"] in ("dining", "passage")]
            if circ:
                b.remove(circ[0])
                b.insert(len(b) // 2, circ[0])
        # keep the kitchen away from toilets: try swapping the kitchen with a neighbour that is not daylight-critical
        def edge_types(node: Node, first: bool) -> set:
            if node.kind == "leaf":
                return {node.spec.type}
            if node.kind == ROW:
                return edge_types(node.children[0] if first else node.children[-1], first)
            return set().union(*(edge_types(ch, first) for ch in node.children))
        def kt_clash(seq) -> bool:
            for a, bnext in zip(seq, seq[1:]):
                ta, tb = edge_types(a["node"], False), edge_types(bnext["node"], True)
                if ("kitchen" in ta and "toilet" in tb) or ("toilet" in ta and "kitchen" in tb):
                    return True
            return False
        if kit and kt_clash(b):
            for j in range(len(b)):
                for k in range(j + 1, len(b)):
                    if needs_ext(b[j]["node"]) or needs_ext(b[k]["node"]):
                        continue
                    b2 = b[:]
                    b2[j], b2[k] = b2[k], b2[j]
                    if not kt_clash(b2):
                        b = b2
                        break
                else:
                    continue
                break
        return [c["node"] for c in b]

    band_nodes = []
    for i, b in enumerate(bands):
        kids = order_band(b, i)
        band_nodes.append(kids[0] if len(kids) == 1 else Node(ROW, kids))
    # passage strip after the last band that holds a circulation room, if rooms lie beyond it
    circ_idx = max((i for i, b in enumerate(bands) if any(c["type"] in ("living", "dining", "foyer") for c in b)), default=0)
    n_after = len(bands) - 1 - circ_idx
    if n_after >= 3:
        return None          # too many bands beyond the circulation: would need two corridors
    if n_after >= 1 and (rng.random() < 0.85 or n_after > 1):
        band_depth = sum(max((c["node"].min_extent(wall)[1] if along_x else c["node"].min_extent(wall)[0]) for c in b) for b in bands)
        if depth is None or band_depth + corridor + wall <= depth:
            psp = RoomSpec("passage", "passage", "Passage", corridor, corridor, corridor * W * 0.8, circulation=True)
            # one band beyond: strip right after the circulation band; two bands: strip between them
            band_nodes.insert(circ_idx + 1 + (1 if n_after == 2 else 0), Node("leaf", spec=psp, rot=False))
        elif n_after > 1:
            return None
    if entry_side in ("top", "right"):
        band_nodes.reverse()
    root = band_nodes[0] if len(band_nodes) == 1 else Node(COL, band_nodes)
    if depth is not None:
        _repair_depth(root, W, depth, wall, along_x)
    return root


def _repair_depth(root: Node, W: float, depth: float, wall: float, along_x: bool) -> None:
    """Flip leaf orientations to reduce the tree's extent across the bands until it fits."""
    for _ in range(12):
        mw, mh = root.min_extent(wall)
        over = (mh > depth + EPS) if along_x else (mw > W + EPS)
        if not over:
            return
        best = None
        for leaf in root.leaves():
            if leaf.spec.min_long == leaf.spec.min_short:
                continue
            leaf.rot = not leaf.rot
            nw, nh = root.min_extent(wall)
            gain = (mh - nh) if along_x else (mw - nw)
            fits_other = (nw <= W + EPS) if along_x else (nh <= depth + EPS)
            if gain > 0 and fits_other and (best is None or gain > best[0]):
                best = (gain, leaf)
            leaf.rot = not leaf.rot
        if best is None:
            return
        best[1].rot = not best[1].rot


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
@dataclass
class UnitContext:
    rect: Rect                                   # clear envelope inside external walls
    external: dict = field(default_factory=dict) # side -> True if external (open air)
    entry_side: str = "bottom"
    north_deg: float = 0.0
    label: str = "Unit"
    shaft_sides: list = field(default_factory=list)   # sides where plumbing shafts can be placed
    zone_bounds: tuple | None = None             # bounds used for the Vastu 3x3 grid (defaults to rect)
    entry_via_stair: bool = False                # upper floors of a house: the staircase is the entry

    def zone_fn(self):
        b = self.zone_bounds or (self.rect.x, self.rect.y, self.rect.x2, self.rect.y2)
        return zone_grid(b, self.north_deg)


@dataclass
class Room:
    spec: RoomSpec
    cell: Rect
    clear: Rect
    external_sides: list = field(default_factory=list)
    door: dict | None = None
    windows: list = field(default_factory=list)
    zone: str = ""
    neighbours: dict = field(default_factory=dict)   # key -> (side, length)
    issues: list = field(default_factory=list)

    @property
    def key(self):
        return self.spec.key

    @property
    def type(self):
        return self.spec.type


@dataclass
class Layout:
    ctx: UnitContext
    tree: Node
    rooms: dict[str, Room]
    hard_ok: bool = True
    violations: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    total: float = 0.0
    warnings: list = field(default_factory=list)
    relaxed: list = field(default_factory=list)


def realise(tree: Node, ctx: UnitContext, cfg: dict, relax: float = 0.0) -> Layout | None:
    """Allocate rects to the tree and build Room objects (walls, neighbours, zones)."""
    t_int = U.parse_length(get(cfg, f"construction.internal_wall_in.{get(cfg, 'construction.wall_material', 'brick')}", 4.5), "in")
    # relax shrinks min dims for a rescue pass
    if relax:
        for leaf in tree.leaves():
            leaf.spec = RoomSpec(**{**leaf.spec.__dict__, "min_short": leaf.spec.min_short * (1 - relax), "min_long": leaf.spec.min_long * (1 - relax)})
    if not allocate(tree, ctx.rect, t_int):
        return None
    rooms: dict[str, Room] = {}
    leaves = tree.leaves()
    zone = ctx.zone_fn()
    for leaf in leaves:
        r = leaf.rect
        ext = [s for s in SIDES if _on_side(r, ctx.rect, s) and ctx.external.get(s)]
        # clear dims: internal sides lose half a wall
        left = 0 if _on_side(r, ctx.rect, "left") else t_int / 2
        right = 0 if _on_side(r, ctx.rect, "right") else t_int / 2
        top = 0 if _on_side(r, ctx.rect, "top") else t_int / 2
        bottom = 0 if _on_side(r, ctx.rect, "bottom") else t_int / 2
        clear = r.shrink(left=left, top=top, right=right, bottom=bottom)
        rooms[leaf.spec.key] = Room(leaf.spec, r, clear, ext, zone=zone(r.cx, r.cy))
    keys = list(rooms)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            sw = shared_wall(rooms[a].cell, rooms[b].cell)
            if sw:
                rooms[a].neighbours[b] = (sw[0], sw[3], sw[1], sw[2])
                rooms[b].neighbours[a] = (OPPOSITE[sw[0]], sw[3], sw[1], sw[2])
    # a habitable room opening onto an external balcony (or a kitchen onto its utility) gets light and air through it
    for rm in rooms.values():
        if rm.type in ("balcony", "utility") and rm.external_sides:
            allowed = ("kitchen",) if rm.type == "utility" else ("master", "bedroom", "guest", "living", "dining", "study")
            hosts = [(length, nk, side) for nk, (side, length, a, b) in rm.neighbours.items()
                     if rooms[nk].type in allowed and length >= 5.0]
            if hosts:
                length, nk, side = max(hosts)
                host = rooms[nk]
                if OPPOSITE[side] not in host.external_sides:
                    host.external_sides.append(OPPOSITE[side])
                    host.issues.append(f"light via balcony ({rm.spec.label})")
                rm.spec = RoomSpec(**{**rm.spec.__dict__, "attach_to": nk})
    return Layout(ctx, tree, rooms)


def _on_side(r: Rect, env: Rect, side: str, tol: float = 0.05) -> bool:
    return {"left": abs(r.x - env.x) < tol, "right": abs(r.x2 - env.x2) < tol,
            "bottom": abs(r.y - env.y) < tol, "top": abs(r.y2 - env.y2) < tol}[side]


def check_hard(layout: Layout, cfg: dict) -> None:
    rooms = layout.rooms
    v: list[str] = []
    door_w = U.parse_length(get(cfg, "doors.internal.w", "3'0\""))
    hard = get(cfg, "rooms.hard_rules", {})
    ar_max = float(get(cfg, "rooms.aspect_ratio_hard_max", 2.2))
    ar_max_living = float(get(cfg, "rooms.aspect_ratio_hard_max_living", 3.0))
    for r in rooms.values():
        s = r.spec
        w, h = r.clear.w, r.clear.h
        if min(w, h) < s.min_short - 0.02 or max(w, h) < s.min_long - 0.02:
            v.append(f"{s.label} is {U.ftin(w)} x {U.ftin(h)}; minimum {U.ftin(s.min_long)} x {U.ftin(s.min_short)}")
        limit = ar_max_living if s.type in ("living", "dining", "shop", "office") else ar_max
        if r.clear.aspect > limit and s.type not in ("passage", "balcony", "utility", "foyer", "pooja", "store", "dress"):
            v.append(f"{s.label} is too elongated ({r.clear.aspect:.1f}:1)")
        if s.external == "required" and not r.external_sides:
            rule = "kitchen_external_wall" if s.type == "kitchen" else "habitable_touches_external_wall"
            if hard.get(rule, True):
                v.append(f"{s.label} has no external wall")
    if hard.get("kitchen_toilet_no_shared_wall", True):
        for r in rooms.values():
            if r.type == "kitchen":
                for nk in r.neighbours:
                    if rooms[nk].type == "toilet":
                        v.append(f"Kitchen shares a wall with {rooms[nk].spec.label}")
    # entry: the entry side of the unit must be touched by living or foyer (or the stair opens to circulation)
    if layout.ctx.entry_via_stair:
        stairs = [r for r in rooms.values() if r.type == "stair"]
        if not stairs:
            v.append("Upper floor has no staircase")
        elif not any(rooms[nk].type in CIRCULATION and ln >= door_w + 0.5 for nk, (sd, ln, a, b) in stairs[0].neighbours.items()):
            v.append("Staircase does not open into the living room or passage")
    else:
        entry_rooms = [r for r in rooms.values() if r.type in ("living", "foyer") and _on_side(r.cell, layout.ctx.rect, layout.ctx.entry_side)]
        if not entry_rooms:
            v.append("Neither living nor foyer touches the entry side")
    # reachability & door hosts
    if hard.get("every_room_reachable_from_circulation", True):
        for r in rooms.values():
            host = pick_door_host(r, layout, door_w, cfg)
            if host is None:
                v.append(f"{r.spec.label} cannot be entered from circulation or its host room")
    layout.violations = v
    layout.hard_ok = not v


def pick_door_host(r: Room, layout: Layout, door_w: float, cfg: dict) -> str | None:
    c = door_host_candidates(r, layout, door_w, cfg)
    return c[0] if c else None


def door_host_candidates(r: Room, layout: Layout, door_w: float, cfg: dict) -> list[str]:
    """Door hosts for room ``r`` in priority order ("ENTRY" first when it is the entrance room)."""
    rooms = layout.rooms
    s = r.spec
    if layout.ctx.entry_via_stair:
        if s.type == "stair":
            return ["ENTRY"]
    elif s.type in ("living", "foyer") and _on_side(r.cell, layout.ctx.rect, layout.ctx.entry_side):
        return ["ENTRY"]
    if s.attach_to and s.attach_to in r.neighbours and r.neighbours[s.attach_to][1] >= door_w + 0.5:
        return [s.attach_to]
    allowed = DOOR_HOST_ALLOWED.get(s.type, CIRCULATION | {"foyer"})
    if s.type == "toilet" and get(cfg, "rooms.hard_rules.toilet_door_not_into_kitchen_or_dining", True):
        allowed = allowed - {"kitchen", "dining"}
    cands = []
    for nk, (side, length, a, b) in r.neighbours.items():
        n = rooms[nk]
        if length < door_w + 0.5:
            continue
        if layout.ctx.entry_via_stair and n.type == "stair" and s.type in CIRCULATION:
            cands.append((0, -length, nk))
            continue
        if n.type in allowed or (n.type in CIRCULATION and s.type not in ("toilet", "utility", "dress")):
            pri = 0 if n.type in ("foyer", "passage") else 1 if n.type == "living" else 2 if n.type == "dining" else 3
            if s.attach_to == nk:
                pri = -1
            cands.append((pri, -length, nk))
    cands.sort()
    return [c[2] for c in cands]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def vastu_room_score(rtype: str, zone: str, cfg: dict, strict_overrides: dict | None = None) -> tuple[float, str]:
    rule = get(cfg, f"vastu.rules.{rtype}")
    if not rule or "good" not in rule:
        return (0.6, "n/a")
    if zone in rule.get("good", []):
        return (1.0, "pass")
    if zone in rule.get("alt", []):
        return (0.65, "partial")
    if zone in rule.get("bad", []):
        return (0.0, "fail")
    return (0.4, "partial")


def score_layout(layout: Layout, cfg: dict, goal: str = "balanced", vastu_mode: str = "preferred") -> None:
    rooms = layout.rooms
    ctx = layout.ctx
    weights = get(cfg, f"scoring.goals.{goal}") or get(cfg, "scoring.goals.balanced")
    ar_pref = float(get(cfg, "rooms.aspect_ratio_preferred_max", 1.5))
    ratio = float(get(cfg, "regulatory.light_ventilation.habitable_window_area_ratio", 0.1))
    win_h = U.parse_length(get(cfg, "windows.default.h", "4'0\""))

    # room quality: aspect + closeness to target area
    q = []
    for r in rooms.values():
        if r.type in ("passage", "foyer", "balcony", "utility"):
            continue
        a = r.clear.aspect
        aq = 1.0 if a <= ar_pref else max(0.0, 1 - (a - ar_pref) / 1.0)
        tq = max(0.0, 1 - abs(r.clear.area - r.spec.target) / max(r.spec.target, 1))
        q.append(0.6 * aq + 0.4 * tq)
    room_quality = sum(q) / len(q) if q else 0.5

    # light: habitable rooms with enough external wall for the window-area rule
    l = []
    for r in rooms.values():
        if not r.spec.habitable:
            continue
        need = ratio * r.clear.area
        avail = 0.0
        for s in r.external_sides:
            avail += (r.clear.w if s in ("top", "bottom") else r.clear.h) - 2.0
        got = max(0.0, avail) * win_h
        l.append(min(1.0, got / need) if need > 0 else 1.0)
    light = sum(l) / len(l) if l else 0.5

    # circulation: passage/foyer share (lower better) and door-host quality
    circ_area = sum(r.cell.area for r in rooms.values() if r.type in ("passage", "foyer"))
    circulation = max(0.0, 1 - circ_area / ctx.rect.area / 0.12)

    # vastu
    vs, vw = 0.0, 0.0
    vastu_detail = {}
    if vastu_mode != "ignore":
        for r in rooms.values():
            rule = get(cfg, f"vastu.rules.{r.type}")
            if not rule or "good" not in rule:
                continue
            sc, status = vastu_room_score(r.type, r.zone, cfg)
            w = float(rule.get("weight", 1))
            vs += sc * w
            vw += w
            vastu_detail[r.key] = {"zone": r.zone, "status": status, "score": sc}
        # brahmasthan open: no toilet/stair centre
        bw = float(get(cfg, "vastu.rules.brahmasthan_open.weight", 0))
        centre_bad = [r for r in rooms.values() if r.type in ("toilet", "stair", "store") and r.zone == "C"]
        vs += (0.0 if centre_bad else 1.0) * bw
        vw += bw
        vastu_detail["brahmasthan_open"] = {"status": "fail" if centre_bad else "pass"}
    vastu = vs / vw if vw else 0.6

    # soft adjacency
    sa_s, sa_w = 0.0, 0.0
    for pref in get(cfg, "rooms.soft_adjacency", []):
        a = [r for r in rooms.values() if r.type == pref["a"]]
        b = [r for r in rooms.values() if r.type == pref["b"]]
        if not a or not b:
            continue
        hit = any(bb.key in aa.neighbours for aa in a for bb in b)
        sa_s += (1.0 if hit else 0.0) * pref["weight"]
        sa_w += pref["weight"]
    soft_adj = sa_s / sa_w if sa_w else 0.7

    # structure: regular grid => number of distinct wall lines (fewer = more regular)
    xs = {round(r.cell.x, 2) for r in rooms.values()} | {round(r.cell.x2, 2) for r in rooms.values()}
    ys = {round(r.cell.y, 2) for r in rooms.values()} | {round(r.cell.y2, 2) for r in rooms.values()}
    n_rooms = max(len(rooms), 1)
    structure = max(0.0, 1 - (len(xs) + len(ys) - 4) / (2.0 * n_rooms))

    # carpet efficiency at unit level: usable (non-circulation) share
    eff = 1 - circ_area / ctx.rect.area
    carpet_efficiency = min(1.0, eff / 0.95)

    sub = {"carpet_efficiency": carpet_efficiency, "vastu": vastu, "room_quality": room_quality,
           "light": light, "circulation": 0.7 * circulation + 0.3 * soft_adj, "structure": structure}
    total = sum(weights.get(k, 0) * v for k, v in sub.items()) / max(sum(weights.values()), 1)
    layout.scores = {k: round(v, 3) for k, v in sub.items()}
    layout.scores["soft_adjacency"] = round(soft_adj, 3)
    layout.scores["vastu_detail"] = vastu_detail
    layout.total = round(total * 100, 1)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def generate(ctx: UnitContext, specs: list[RoomSpec], cfg: dict, goal: str = "balanced", vastu_mode: str = "preferred",
             n_candidates: int | None = None, seed: int | None = None, keep: int = 5,
             fixed_tree: Node | None = None) -> list[Layout]:
    """Generate ranked feasible layouts for one unit. Returns up to ``keep`` layouts (best first)."""
    n = n_candidates or int(get(cfg, "scoring.candidates_per_option", 400))
    rng = random.Random(get(cfg, "scoring.random_seed", 7) if seed is None else seed)
    results: list[Layout] = []
    seen: set[str] = set()
    relax_ladder = [0.0, 0.05, 0.10, 0.15]
    t_int = U.parse_length(get(cfg, f"construction.internal_wall_in.{get(cfg, 'construction.wall_material', 'brick')}", 4.5), "in")
    along = ctx.rect.w if ctx.entry_side in ("bottom", "top") else ctx.rect.h
    budget = int(get(cfg, "scoring.max_tree_attempts", 8000))
    for relax in relax_ladder:
        realised = 0
        attempts = 0
        max_attempts = budget
        rspecs = [RoomSpec(**{**sp.__dict__, "min_short": sp.min_short * (1 - relax), "min_long": sp.min_long * (1 - relax)}) for sp in specs] if relax else specs
        while realised < n and attempts < max_attempts and len(results) < n:
            attempts += 1
            if fixed_tree is not None:
                tree = fixed_tree
                if attempts > 1:
                    break
            else:
                tree = random_tree(rspecs, rng, ctx.entry_side, along, depth=(ctx.rect.h if ctx.entry_side in ("bottom", "top") else ctx.rect.w),
                                   wall=t_int, external=ctx.external, stair_entry=ctx.entry_via_stair,
                                   corridor=U.parse_length(get(cfg, "regulatory.circulation.min_corridor_ft", 4.0)))
                if tree is None:
                    continue
                mw, mh = tree.min_extent(t_int)
                if mw > ctx.rect.w + EPS or mh > ctx.rect.h + EPS:
                    continue
            lay = realise(tree, ctx, cfg, relax=relax if fixed_tree is not None else 0.0)
            if lay is None:
                continue
            realised += 1
            check_hard(lay, cfg)
            if not lay.hard_ok:
                continue
            sig = "|".join(f"{k}:{round(r.cell.x,1)},{round(r.cell.y,1)},{round(r.cell.w,1)},{round(r.cell.h,1)}" for k, r in sorted(lay.rooms.items()))
            if sig in seen:
                continue
            seen.add(sig)
            score_layout(lay, cfg, goal, vastu_mode)
            if relax:
                lay.relaxed.append(f"Minimum room sizes relaxed by {int(relax*100)}% to fit the envelope")
            results.append(lay)
        if results:
            break
    if vastu_mode == "strict" and results:
        strict = [l for l in results if all(d.get("status") != "fail" for d in l.scores["vastu_detail"].values())]
        if strict:
            results = strict
        else:
            for l in results:
                l.warnings.append("No candidate satisfies every Strict Vastu rule without breaking a hard constraint; nearest alternative shown.")
    results.sort(key=lambda l: -l.total)
    # diversity: prefer distinct room arrangements among the kept set
    kept: list[Layout] = []
    for l in results:
        if len(kept) >= keep:
            break
        if all(_arrangement_distance(l, k) > 0.25 for k in kept):
            kept.append(l)
    for l in results:
        if len(kept) >= keep:
            break
        if l not in kept:
            kept.append(l)
    return kept


def _relax_tree(tree: Node, relax: float) -> None:
    for leaf in tree.leaves():
        sp = leaf.spec
        leaf.spec = RoomSpec(**{**sp.__dict__, "min_short": sp.min_short * (1 - relax), "min_long": sp.min_long * (1 - relax)})


def _arrangement_distance(a: Layout, b: Layout) -> float:
    d, n = 0.0, 0
    for k, ra in a.rooms.items():
        rb = b.rooms.get(k)
        if rb is None:
            continue
        d += math.hypot(ra.cell.cx - rb.cell.cx, ra.cell.cy - rb.cell.cy) / max(a.ctx.rect.w, a.ctx.rect.h)
        n += 1
    return d / max(n, 1)
