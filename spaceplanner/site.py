"""Site model: plot boundary, north, roads, setbacks, buildable envelope and regulatory maths.

Coordinate frame: decimal feet, x to the right, y UP (CAD convention). North is given
as ``north_deg``: degrees measured clockwise from +y to true north (0 = north is up).
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field

from shapely.geometry import Polygon, Point, LineString, box
from shapely.ops import unary_union

from . import units as U
from .config import get

COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def bearing_of_vector(dx: float, dy: float, north_deg: float) -> float:
    """Compass bearing (0..360, clockwise from true north) of a frame vector."""
    frame_ang = math.degrees(math.atan2(dx, dy))  # 0 = +y (up), clockwise
    return (frame_ang - north_deg) % 360


def compass_of_vector(dx: float, dy: float, north_deg: float) -> str:
    return COMPASS[int(round(bearing_of_vector(dx, dy, north_deg) / 45)) % 8]


def vector_of_compass(code: str, north_deg: float) -> tuple[float, float]:
    b = math.radians(COMPASS.index(code) * 45 + north_deg)
    return (math.sin(b), math.cos(b))


@dataclass
class Edge:
    index: int
    a: tuple[float, float]
    b: tuple[float, float]
    length: float
    normal: tuple[float, float]      # outward unit normal
    compass: str                     # compass direction the edge faces
    role: str = "side"               # front | rear | side
    road_width_m: float | None = None
    adjoining: str = "open plot"     # open plot | building | drain | road
    setback_ft: float = 0.0
    setback_source: str = ""

    @property
    def mid(self) -> tuple[float, float]:
        return ((self.a[0] + self.b[0]) / 2, (self.a[1] + self.b[1]) / 2)


@dataclass
class Plot:
    polygon: Polygon
    north_deg: float
    edges: list[Edge]
    roads: dict[int, float]                  # edge index -> road width (m)
    corner: bool
    entry_edge: int | None
    vehicle_edge: int | None
    obstacles: list[dict] = field(default_factory=list)
    ground_vs_road_ft: float = 0.0

    @property
    def area(self) -> float:
        return self.polygon.area

    @property
    def bounds(self):
        return self.polygon.bounds

    def edge_by_ref(self, ref) -> Edge | None:
        """Resolve an edge reference: int index, 'edge:2', frame side (top/right/bottom/left) or compass code."""
        if ref is None:
            return None
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            i = int(ref)
            return self.edges[i] if 0 <= i < len(self.edges) else None
        s = str(ref).strip().lower()
        if s.startswith("edge:"):
            return self.edge_by_ref(int(s[5:]))
        frame = {"top": (0, 1), "bottom": (0, -1), "left": (-1, 0), "right": (1, 0)}
        if s in frame:
            return self._closest_edge_to_vector(frame[s])
        if s.upper() in COMPASS:
            return self._closest_edge_to_vector(vector_of_compass(s.upper(), self.north_deg))
        return None

    def _closest_edge_to_vector(self, v) -> Edge:
        return max(self.edges, key=lambda e: e.normal[0] * v[0] + e.normal[1] * v[1])


# ---------------------------------------------------------------------------
# Construction from a brief
# ---------------------------------------------------------------------------

def _polygon_from_brief(site: dict, cfg: dict) -> Polygon:
    land = U.LandUnits(get(cfg, "units.kattha_sqft", 1361.25), get(cfg, "units.dhur_per_kattha", 20))
    if site.get("vertices"):
        pts = [(U.parse_length(p[0]), U.parse_length(p[1])) for p in site["vertices"]]
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.exterior.is_ccw is False:
            poly = Polygon(list(poly.exterior.coords)[::-1])
        return poly
    length = site.get("length")
    breadth = site.get("breadth")
    if length is not None and breadth is not None:
        L, B = U.parse_length(length), U.parse_length(breadth)
    else:
        # Area-only input: derive a rectangle from area and an aspect ratio (default 1.5 deep)
        area = land.to_sqft(kattha=site.get("kattha", 0) or 0, dhur=site.get("dhur", 0) or 0,
                            sqft=site.get("area_sqft", 0) or 0, sqm=site.get("area_sqm", 0) or 0)
        if area <= 0:
            raise ValueError("Site needs length x breadth, vertices, or an area in sq ft / kattha / dhur")
        aspect = float(site.get("aspect", 1.5))
        L = math.sqrt(area / aspect)
        B = area / L
    # Frontage L along x (bottom edge is index 0 and the default front), depth B along y.
    return Polygon([(0, 0), (L, 0), (L, B), (0, B)])


def build_plot(site: dict, cfg: dict) -> Plot:
    poly = _polygon_from_brief(site, cfg)
    north = float(site.get("north_deg", 0.0))
    coords = list(poly.exterior.coords)[:-1]
    edges: list[Edge] = []
    n = len(coords)
    for i in range(n):
        a, b = coords[i], coords[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        # CCW polygon: outward normal is the right-hand normal of the edge direction
        nx, ny = dy / length, -dx / length
        edges.append(Edge(i, a, b, length, (nx, ny), compass_of_vector(nx, ny, north)))
    plot = Plot(poly, north, edges, {}, bool(site.get("corner", False)), None, None,
                obstacles=list(site.get("obstacles", [])), ground_vs_road_ft=U.parse_length(site.get("ground_vs_road", 0)))

    roads = site.get("roads") or []
    if not roads:
        roads = [{"side": "bottom", "width_m": 9.0}]
    for rd in roads:
        e = plot.edge_by_ref(rd.get("side", rd.get("edge")))
        if e is None:
            continue
        w = rd.get("width_m")
        if w is None and rd.get("width") is not None:
            w = U.ft_to_m(U.parse_length(rd["width"]))
        e.road_width_m = float(w or 9.0)
        e.adjoining = "road"
        plot.roads[e.index] = e.road_width_m
    if len(plot.roads) >= 2:
        plot.corner = True
    for adj in site.get("adjoining", []) or []:
        e = plot.edge_by_ref(adj.get("side"))
        if e is not None and e.index not in plot.roads:
            e.adjoining = adj.get("type", "open plot")

    # roles: primary front = widest road; corner => second road also front (config); rear = most opposite
    road_edges = sorted(plot.roads.items(), key=lambda kv: -kv[1])
    primary = plot.edges[road_edges[0][0]]
    primary.role = "front"
    if plot.corner and get(cfg, "regulatory.corner_plot_second_front", True):
        for idx, _ in road_edges[1:]:
            plot.edges[idx].role = "front"
    pn = primary.normal
    rear = min((e for e in plot.edges if e.role != "front"), key=lambda e: e.normal[0] * pn[0] + e.normal[1] * pn[1], default=None)
    if rear is not None:
        rear.role = "rear"

    entry = plot.edge_by_ref(site.get("entry_side")) or primary
    vehicle = plot.edge_by_ref(site.get("vehicle_entry_side")) or primary
    plot.entry_edge, plot.vehicle_edge = entry.index, vehicle.index
    return plot


# ---------------------------------------------------------------------------
# Regulatory lookups
# ---------------------------------------------------------------------------

def lookup_far(plot: Plot, cfg: dict) -> dict:
    road_w = max(plot.roads.values()) if plot.roads else 6.0
    for row in get(cfg, "regulatory.far_by_road_width", []):
        if road_w <= row["max_road_m"]:
            return {"far": row["far"], "coverage_pct": row["coverage_pct"], "max_height_m": row["max_height_m"], "road_width_m": road_w}
    row = get(cfg, "regulatory.far_by_road_width")[-1]
    return {"far": row["far"], "coverage_pct": row["coverage_pct"], "max_height_m": row["max_height_m"], "road_width_m": road_w}


def lookup_setbacks(plot: Plot, building_height_ft: float, cfg: dict) -> dict:
    """Setbacks in feet by role, from plot area and building height tables (max of both)."""
    area_sqm = U.sqft_to_sqm(plot.area)
    row = None
    for r in get(cfg, "regulatory.setbacks_by_plot_area_m", []):
        if area_sqm <= r["max_area_sqm"]:
            row = r
            break
    row = row or get(cfg, "regulatory.setbacks_by_plot_area_m")[-1]
    h_m = U.ft_to_m(building_height_ft)
    min_all = 0.0
    for r in get(cfg, "regulatory.setbacks_by_height_m", []):
        if h_m <= r["max_height_m"]:
            min_all = r["min_all"]
            break
    out = {}
    for role_key, role in (("front", "front"), ("rear", "rear"), ("side1", "side1"), ("side2", "side2")):
        out[role] = U.m_to_ft(max(row[role_key], min_all))
    out["_source"] = f"plot {area_sqm:.0f} sq m table row (<= {row['max_area_sqm']} sq m), height {h_m:.1f} m min {min_all} m"
    return out


def apply_setbacks(plot: Plot, building_height_ft: float, cfg: dict, overrides: dict | None = None) -> dict:
    """Assign a setback to every edge and build the buildable envelope.

    ``overrides`` maps edge role or edge index to a setback (any length format).
    Returns dict with envelope polygon, per-edge setbacks and warnings.
    """
    table = lookup_setbacks(plot, building_height_ft, cfg)
    warnings = [f"Setbacks taken from default table: {table['_source']}. Verify with {get(cfg, 'regulatory.authority', 'authority')}."]
    side_roles = [e for e in plot.edges if e.role == "side"]
    # Larger side setback goes on the longer side edge
    side_roles.sort(key=lambda e: -e.length)
    for i, e in enumerate(side_roles):
        e.setback_ft = table["side1"] if i == 0 else table["side2"]
        e.setback_source = "side1" if i == 0 else "side2"
    for e in plot.edges:
        if e.role == "front":
            e.setback_ft, e.setback_source = table["front"], "front"
        elif e.role == "rear":
            e.setback_ft, e.setback_source = table["rear"], "rear"
    if overrides:
        for key, val in overrides.items():
            v = U.parse_length(val, "ft")
            for e in plot.edges:
                if str(key).lower() in (e.role, str(e.index), e.setback_source, e.compass.lower()):
                    e.setback_ft, e.setback_source = v, f"user override ({key})"
            warnings.append(f"Setback override applied for {key}: {U.ftin(v)}")

    env = plot.polygon
    for e in plot.edges:
        if e.setback_ft <= 0:
            continue
        env = env.intersection(_half_plane(e, e.setback_ft, plot))
    # corner visibility splay at road/road corners
    splay = U.parse_length(get(cfg, "regulatory.corner_splay_ft", 0), "ft")
    if plot.corner and splay > 0:
        n = len(plot.edges)
        for e in plot.edges:
            nxt = plot.edges[(e.index + 1) % n]
            if e.index in plot.roads and nxt.index in plot.roads:
                c = e.b
                ex = ((e.a[0] - e.b[0]) / e.length, (e.a[1] - e.b[1]) / e.length)
                nx = ((nxt.b[0] - nxt.a[0]) / nxt.length, (nxt.b[1] - nxt.a[1]) / nxt.length)
                tri = Polygon([c, (c[0] + ex[0] * splay, c[1] + ex[1] * splay), (c[0] + nx[0] * splay, c[1] + nx[1] * splay)])
                plot.polygon = plot.polygon.difference(tri)
                env = env.difference(tri)
                warnings.append(f"Corner splay of {U.ftin(splay)} applied at the road junction (edge {e.index}/{nxt.index}).")
    if env.is_empty or env.area <= 0:
        raise ValueError("Setbacks leave no buildable area on this plot")
    if env.geom_type == "MultiPolygon":
        env = max(env.geoms, key=lambda g: g.area)
    return {"envelope": env, "setbacks": {e.index: e.setback_ft for e in plot.edges}, "table": table, "warnings": warnings}


def _half_plane(e: Edge, dist: float, plot: Plot) -> Polygon:
    """Region of the plane at least ``dist`` inside edge ``e`` (opposite its outward normal)."""
    big = 1e5
    nx, ny = e.normal
    ax, ay = e.a[0] - nx * dist, e.a[1] - ny * dist
    tx, ty = -ny, nx  # along-edge direction
    p1 = (ax + tx * big, ay + ty * big)
    p2 = (ax - tx * big, ay - ty * big)
    p3 = (p2[0] - nx * big, p2[1] - ny * big)
    p4 = (p1[0] - nx * big, p1[1] - ny * big)
    return Polygon([p1, p2, p3, p4])


def largest_inscribed_rect(poly: Polygon, step: float = 1.0) -> tuple[float, float, float, float]:
    """Largest axis-aligned rectangle inside a polygon (grid search; exact for rectangles)."""
    minx, miny, maxx, maxy = poly.bounds
    if abs(poly.area - (maxx - minx) * (maxy - miny)) < 1e-6:
        return (minx, miny, maxx - minx, maxy - miny)
    best = (minx, miny, 0.0, 0.0)
    xs = [minx + i * step for i in range(int((maxx - minx) / step) + 1)] + [maxx]
    ys = [miny + i * step for i in range(int((maxy - miny) / step) + 1)] + [maxy]
    for i, x1 in enumerate(xs):
        for x2 in xs[i + 1:]:
            if (x2 - x1) * (maxy - miny) <= best[2] * best[3]:
                continue
            for j, y1 in enumerate(ys):
                for y2 in reversed(ys[j + 1:]):
                    if (x2 - x1) * (y2 - y1) <= best[2] * best[3]:
                        break
                    if poly.contains(box(x1, y1, x2, y2).buffer(-1e-6)):
                        best = (x1, y1, x2 - x1, y2 - y1)
                        break
    return best


def regulatory_summary(plot: Plot, cfg: dict, floors: int, floor_to_floor_ft: float, stilt: bool = False, basement: bool = False) -> dict:
    far = lookup_far(plot, cfg)
    height_ft = floors * floor_to_floor_ft + (U.parse_length(get(cfg, "building.stilt_height_ft", 8.5)) if stilt else 0) + U.parse_length(get(cfg, "building.plinth_height_ft", 2))
    max_h_ft = U.m_to_ft(far["max_height_m"])
    fire_trigger_ft = U.m_to_ft(get(cfg, "regulatory.fire.high_rise_trigger_m", 15))
    warnings = [f"FAR {far['far']} and ground coverage {far['coverage_pct']}% taken from default table for a {far['road_width_m']} m road. Verify with authority."]
    if height_ft > max_h_ft:
        warnings.append(f"Proposed height {U.ftin(height_ft)} exceeds the default maximum {U.ftin(max_h_ft)} for this road width.")
    return {
        "far_permitted": far["far"],
        "far_purchasable": get(cfg, "regulatory.purchasable_far", 0),
        "coverage_pct_max": far["coverage_pct"],
        "max_height_ft": max_h_ft,
        "building_height_ft": height_ft,
        "high_rise": height_ft > fire_trigger_ft,
        "lift_required": floors > get(cfg, "regulatory.lift.mandatory_above_floors", 4),
        "permitted_builtup_sqft": far["far"] * plot.area,
        "permitted_builtup_with_purchase_sqft": (far["far"] + get(cfg, "regulatory.purchasable_far", 0)) * plot.area,
        "max_footprint_sqft": far["coverage_pct"] / 100 * plot.area,
        "warnings": warnings,
        "source": get(cfg, "regulatory.source"),
    }


def zone_grid(poly_bounds, north_deg: float):
    """Return a function mapping (x, y) -> Vastu zone code using a 3x3 grid on the
    bounding box of the geometry rotated so that true north points up."""
    minx, miny, maxx, maxy = poly_bounds
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    ang = math.radians(north_deg)
    # rotate corners into north-up frame to get the rotated bounding box
    def rot(px, py):
        dx, dy = px - cx, py - cy
        return (dx * math.cos(ang) - dy * math.sin(ang), dx * math.sin(ang) + dy * math.cos(ang))
    corners = [rot(minx, miny), rot(maxx, miny), rot(maxx, maxy), rot(minx, maxy)]
    rx = [c[0] for c in corners]
    ry = [c[1] for c in corners]
    rminx, rmaxx, rminy, rmaxy = min(rx), max(rx), min(ry), max(ry)
    w, h = rmaxx - rminx, rmaxy - rminy

    def zone(px, py):
        qx, qy = rot(px, py)
        col = 0 if qx < rminx + w / 3 else 1 if qx < rminx + 2 * w / 3 else 2
        row = 2 if qy < rminy + h / 3 else 1 if qy < rminy + 2 * h / 3 else 0   # row 0 = north
        table = [["NW", "N", "NE"], ["W", "C", "E"], ["SW", "S", "SE"]]
        return table[row][col]
    return zone
