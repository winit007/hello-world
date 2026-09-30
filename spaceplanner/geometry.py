"""Axis-aligned rectangle helpers used by the layout engine (decimal feet, y up: top = max y)."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict

EPS = 1e-6


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    @property
    def area(self) -> float:
        return self.w * self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def aspect(self) -> float:
        return max(self.w, self.h) / max(min(self.w, self.h), EPS)

    def shrink(self, left=0.0, top=0.0, right=0.0, bottom=0.0) -> "Rect":
        return Rect(self.x + left, self.y + top, self.w - left - right, self.h - top - bottom)

    def contains_point(self, px: float, py: float) -> bool:
        return self.x - EPS <= px <= self.x2 + EPS and self.y - EPS <= py <= self.y2 + EPS

    def intersects(self, o: "Rect") -> bool:
        return overlap_area(self, o) > EPS

    def as_dict(self) -> dict:
        return asdict(self)

    def polygon(self) -> list[tuple[float, float]]:
        return [(self.x, self.y), (self.x2, self.y), (self.x2, self.y2), (self.x, self.y2)]


def overlap_area(a: Rect, b: Rect) -> float:
    w = min(a.x2, b.x2) - max(a.x, b.x)
    h = min(a.y2, b.y2) - max(a.y, b.y)
    return w * h if w > EPS and h > EPS else 0.0


def shared_wall(a: Rect, b: Rect, tol: float = 0.05) -> tuple[str, float, float, float] | None:
    """Return (side_of_a, start, end, length) of the wall a shares with b, or None.

    side_of_a is which side of ``a`` touches ``b``: left/right/top/bottom.
    start/end are along the shared axis.
    """
    oy = (max(a.y, b.y), min(a.y2, b.y2))
    ox = (max(a.x, b.x), min(a.x2, b.x2))
    if abs(a.x2 - b.x) < tol and oy[1] - oy[0] > tol:
        return ("right", oy[0], oy[1], oy[1] - oy[0])
    if abs(b.x2 - a.x) < tol and oy[1] - oy[0] > tol:
        return ("left", oy[0], oy[1], oy[1] - oy[0])
    if abs(a.y2 - b.y) < tol and ox[1] - ox[0] > tol:
        return ("top", ox[0], ox[1], ox[1] - ox[0])
    if abs(b.y2 - a.y) < tol and ox[1] - ox[0] > tol:
        return ("bottom", ox[0], ox[1], ox[1] - ox[0])
    return None


def side_segment(r: Rect, side: str) -> tuple[tuple[float, float], tuple[float, float]]:
    if side == "top":
        return ((r.x, r.y2), (r.x2, r.y2))
    if side == "bottom":
        return ((r.x, r.y), (r.x2, r.y))
    if side == "left":
        return ((r.x, r.y), (r.x, r.y2))
    return ((r.x2, r.y), (r.x2, r.y2))


OPPOSITE = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}
