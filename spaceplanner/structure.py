"""Structural grid: regular column lines snapped to walls, columns kept out of rooms and doorways."""
from __future__ import annotations
import string

from . import units as U
from .config import get
from .geometry import Rect

TOL = 0.1


def wall_segments(room_cells: list[Rect], plate: Rect) -> dict:
    """Vertical and horizontal wall lines from room cells and the outer plate."""
    vx: list[tuple[float, float, float]] = [(plate.x, plate.y, plate.y2), (plate.x2, plate.y, plate.y2)]
    hy: list[tuple[float, float, float]] = [(plate.y, plate.x, plate.x2), (plate.y2, plate.x, plate.x2)]
    for c in room_cells:
        vx += [(c.x, c.y, c.y2), (c.x2, c.y, c.y2)]
        hy += [(c.y, c.x, c.x2), (c.y2, c.x, c.x2)]
    return {"x": vx, "y": hy}


def _pick_lines(start: float, end: float, coords: dict[float, float], min_sp: float, max_sp: float, warnings: list, axis: str) -> list[float]:
    lines = [start]
    last = start
    while end - last > TOL:
        cands = [c for c in coords if last + min_sp - TOL <= c <= last + max_sp + TOL and c <= end + TOL]
        if end - last <= max_sp + TOL:
            lines.append(end)
            break
        if cands:
            # prefer a wall line with the most support that leaves a regular remainder
            nxt = max(cands, key=lambda c: (coords[c] > 2.0, c))
        else:
            near = [c for c in coords if last + 3.0 <= c <= last + max_sp + TOL and c <= end + TOL]
            if near:
                nxt = max(near)                      # closer than the preferred minimum, but within the span limit
            else:
                far = [c for c in coords if c > last + TOL]
                nxt = min(far) if far else end
                if nxt - last > max_sp + TOL:
                    # no wall to land on: drop a column line at the span limit anyway (beam only, flagged)
                    nxt = min(last + max_sp, end)
                    warnings.append(f"No wall line within {U.ftin(max_sp)} along {axis} after {U.ftin(last - start)}; a free-standing column line was added, check with the structural engineer.")
        lines.append(nxt)
        last = nxt
    return lines


def column_grid(plate: Rect, room_cells: list[Rect], cfg: dict, kind: str = "house", doors: list[tuple] | None = None) -> dict:
    """Return column grid lines, columns (only where a wall exists) and a CSV export."""
    max_sp = U.parse_length(get(cfg, "construction.max_beam_span_ft", 16))
    min_sp = U.parse_length(get(cfg, "construction.min_column_spacing_ft", 8))
    size = get(cfg, f"construction.column_size_in.{kind}") or get(cfg, "construction.column_size_in.house")
    cw, cd = U.parse_length(size[0], "in"), U.parse_length(size[1], "in")
    walls = wall_segments(room_cells, plate)
    warnings: list[str] = []
    # support weight = total wall length on each coordinate
    xs: dict[float, float] = {}
    for x, y1, y2 in walls["x"]:
        xs[round(x, 2)] = xs.get(round(x, 2), 0) + (y2 - y1)
    ys: dict[float, float] = {}
    for y, x1, x2 in walls["y"]:
        ys[round(y, 2)] = ys.get(round(y, 2), 0) + (x2 - x1)
    gx = _pick_lines(plate.x, plate.x2, xs, min_sp, max_sp, warnings, "X")
    gy = _pick_lines(plate.y, plate.y2, ys, min_sp, max_sp, warnings, "Y")

    def on_wall(x: float, y: float) -> bool:
        for wx, y1, y2 in walls["x"]:
            if abs(wx - x) < TOL and y1 - TOL <= y <= y2 + TOL:
                return True
        for wy, x1, x2 in walls["y"]:
            if abs(wy - y) < TOL and x1 - TOL <= x <= x2 + TOL:
                return True
        return False

    def in_door(x: float, y: float) -> bool:
        for (x1, y1, x2, y2) in doors or []:
            if min(x1, x2) - cw <= x <= max(x1, x2) + cw and min(y1, y2) - cd <= y <= max(y1, y2) + cd:
                return True
        return False

    columns = []
    skipped = 0
    labels_x = [_letter(i) for i in range(len(gx))]
    labels_y = [str(i + 1) for i in range(len(gy))]
    for i, x in enumerate(gx):
        for j, y in enumerate(gy):
            if not on_wall(x, y):
                skipped += 1
                continue
            if in_door(x, y):
                warnings.append(f"Column {labels_x[i]}{labels_y[j]} clashes with a doorway; shift the door or the column.")
            # keep the column inside the plate at the edges
            cx = min(max(x, plate.x + cw / 2), plate.x2 - cw / 2)
            cy = min(max(y, plate.y + cd / 2), plate.y2 - cd / 2)
            columns.append({"id": f"{labels_x[i]}{labels_y[j]}", "grid_x": labels_x[i], "grid_y": labels_y[j],
                            "x": cx, "y": cy, "gx": x, "gy": y, "w": cw, "d": cd, "rect": Rect(cx - cw / 2, cy - cd / 2, cw, cd)})
    if skipped:
        warnings.append(f"{skipped} grid intersections fall inside rooms; no column placed there (beam only). Consider aligning walls to the grid.")
    csv = ["id,grid_x,grid_y,x_ft,y_ft,x_m,y_m,size_in"]
    for c in columns:
        csv.append(f"{c['id']},{c['grid_x']},{c['grid_y']},{c['x']:.3f},{c['y']:.3f},{U.ft_to_m(c['x']):.3f},{U.ft_to_m(c['y']):.3f},{size[0]}x{size[1]}")
    spans_x = [gx[i + 1] - gx[i] for i in range(len(gx) - 1)]
    spans_y = [gy[i + 1] - gy[i] for i in range(len(gy) - 1)]
    regularity = 1.0
    if spans_x and spans_y:
        import statistics
        regularity = max(0.0, 1 - (statistics.pstdev(spans_x) / max(statistics.mean(spans_x), 1) + statistics.pstdev(spans_y) / max(statistics.mean(spans_y), 1)) / 2)
    return {"grid_x": gx, "grid_y": gy, "labels_x": labels_x, "labels_y": labels_y, "columns": columns,
            "column_size_in": size, "csv": "\n".join(csv), "warnings": warnings, "regularity": round(regularity, 3)}


def _letter(i: int) -> str:
    s = ""
    i += 1
    while i > 0:
        i, r = divmod(i - 1, 26)
        s = string.ascii_uppercase[r] + s
    return s
