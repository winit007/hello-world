"""SVG previews: floor plans and the site plan (plan frame is y-up; SVG is y-down)."""
from __future__ import annotations
import math
from xml.sax.saxutils import escape

from .. import units as U

ROOM_FILL = {"living": "#fde9b5", "dining": "#fdd9a8", "kitchen": "#ffc4ad", "master": "#d9ccf2", "bedroom": "#e6dcf7", "guest": "#e6dcf7",
             "servant": "#e6dcf7", "toilet": "#c4e4f2", "utility": "#e0efe0", "pooja": "#fff2b3", "study": "#d3ecc4", "balcony": "#dff0d3",
             "store": "#e6e1d6", "passage": "#f1f1f1", "foyer": "#f1f1f1", "dress": "#efe6fa", "stair": "#e3e3e3", "shop": "#fbe1c8",
             "office": "#fbe9d0", "core": "#dcdcdc", "corridor": "#f3f3f3", "parking": "#f6f6f6"}


class Canvas:
    def __init__(self, bounds, width_px: int, pad: float = 30.0):
        minx, miny, maxx, maxy = bounds
        self.minx, self.miny, self.maxx, self.maxy = minx, miny, maxx, maxy
        self.s = (width_px - 2 * pad) / max(maxx - minx, 1e-6)
        self.pad = pad
        self.w = width_px
        self.h = (maxy - miny) * self.s + 2 * pad
        self.parts: list[str] = []

    def P(self, x: float, y: float) -> tuple[float, float]:
        return (self.pad + (x - self.minx) * self.s, self.pad + (self.maxy - y) * self.s)

    def rect(self, r: dict, fill="none", stroke="#222", sw=1.0, dash=None, cls="", extra=""):
        x, y = self.P(r["x"], r["y"] + r["h"])
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{r["w"]*self.s:.1f}" height="{r["h"]*self.s:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d} class="{cls}" {extra}/>')

    def poly(self, pts, fill="none", stroke="#222", sw=1.0, dash=None, cls=""):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        p = " ".join(f"{self.P(x, y)[0]:.1f},{self.P(x, y)[1]:.1f}" for x, y in pts)
        self.parts.append(f'<polygon points="{p}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d} class="{cls}"/>')

    def line(self, x1, y1, x2, y2, stroke="#222", sw=1.0, dash=None):
        a, b = self.P(x1, y1), self.P(x2, y2)
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def text(self, x, y, s, size=10, anchor="middle", fill="#222", weight="normal", rotate=0, cls=""):
        px, py = self.P(x, y)
        rot = f' transform="rotate({rotate} {px:.1f} {py:.1f})"' if rotate else ""
        self.parts.append(f'<text x="{px:.1f}" y="{py:.1f}" font-size="{size}" text-anchor="{anchor}" fill="{fill}" font-weight="{weight}" dominant-baseline="middle"{rot} class="{cls}">{escape(str(s))}</text>')

    def arc(self, cx, cy, r, a0, a1, stroke="#444", sw=0.8):
        # angles in degrees, plan frame (counter-clockwise, 0 = +x)
        x0, y0 = self.P(cx + r * math.cos(math.radians(a0)), cy + r * math.sin(math.radians(a0)))
        x1, y1 = self.P(cx + r * math.cos(math.radians(a1)), cy + r * math.sin(math.radians(a1)))
        large = 1 if abs(a1 - a0) > 180 else 0
        sweep = 0 if a1 > a0 else 1     # y is flipped in SVG
        self.parts.append(f'<path d="M {x0:.1f} {y0:.1f} A {r*self.s:.1f} {r*self.s:.1f} 0 {large} {sweep} {x1:.1f} {y1:.1f}" fill="none" stroke="{stroke}" stroke-width="{sw}"/>')

    def render(self, title: str = "") -> str:
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w:.0f} {self.h:.0f}" width="{self.w:.0f}" height="{self.h:.0f}" '
                f'font-family="Segoe UI, Helvetica, Arial, sans-serif">'
                f'<title>{escape(title)}</title><rect width="100%" height="100%" fill="#ffffff"/>')
        return head + "".join(self.parts) + "</svg>"


def north_arrow(c: Canvas, x: float, y: float, north_deg: float, size: float = 2.5):
    ang = math.radians(north_deg)
    dx, dy = math.sin(ang), math.cos(ang)
    c.line(x - dx * size, y - dy * size, x + dx * size, y + dy * size, "#c0392b", 1.5)
    tip = (x + dx * size, y + dy * size)
    px, py = c.P(*tip)
    c.parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3" fill="#c0392b"/>')
    c.text(x + dx * (size + 1.5), y + dy * (size + 1.5), "N", 11, fill="#c0392b", weight="bold")


def _door(c: Canvas, d: dict):
    x1, y1, x2, y2 = d["seg"]
    if d.get("opening"):
        c.line(x1, y1, x2, y2, "#ffffff", 3.0)
        c.line(x1, y1, x2, y2, "#999", 0.8, dash="3,3")
        return
    c.line(x1, y1, x2, y2, "#ffffff", 3.2)      # clear the wall
    w = d["w"]
    horiz = abs(y2 - y1) < 1e-6
    if d.get("sliding"):
        if horiz:
            c.line(x1, y1 - 0.25, x1 + w / 2, y1 - 0.25, "#444", 1.2); c.line(x1 + w / 2, y1 + 0.25, x2, y1 + 0.25, "#444", 1.2)
        else:
            c.line(x1 - 0.25, y1, x1 - 0.25, y1 + w / 2, "#444", 1.2); c.line(x1 + 0.25, y1 + w / 2, x1 + 0.25, y2, "#444", 1.2)
        return
    # swing leaf + arc, opening into the room on the given side
    side = d["side"]
    if horiz:
        hx, hy = x1, y1
        into = -1 if side == "top" else 1       # room lies below its top wall
        c.line(hx, hy, hx, hy + into * w, "#444", 1.2)
        c.arc(hx, hy, w, 0 if into > 0 else -90, 90 if into > 0 else 0)
    else:
        hx, hy = x1, y1
        into = -1 if side == "right" else 1
        c.line(hx, hy, hx + into * w, hy, "#444", 1.2)
        c.arc(hx, hy, w, 0 if into > 0 else 90, 90 if into > 0 else 180)


def _window(c: Canvas, wd: dict):
    x1, y1, x2, y2 = wd["seg"]
    c.line(x1, y1, x2, y2, "#ffffff", 3.2)
    if abs(y2 - y1) < 1e-6:
        c.line(x1, y1 - 0.2, x2, y1 - 0.2, "#2a7ab8", 1.0); c.line(x1, y1 + 0.2, x2, y1 + 0.2, "#2a7ab8", 1.0)
    else:
        c.line(x1 - 0.2, y1, x1 - 0.2, y2, "#2a7ab8", 1.0); c.line(x1 + 0.2, y1, x1 + 0.2, y2, "#2a7ab8", 1.0)
    c.line(x1, y1, x2, y2, "#2a7ab8", 0.6)


def render_floor(option: dict, floor_index: int, width_px: int = 1000, show_grid: bool = True, show_zones: bool = False) -> str:
    f = option["floors"][floor_index]
    plate = f["plate"]
    plot = option["plot"]
    pad_ft = 8.0
    bounds = (plate["x"] - pad_ft, plate["y"] - pad_ft, plate["x"] + plate["w"] + pad_ft, plate["y"] + plate["h"] + pad_ft + 4)
    c = Canvas(bounds, width_px)
    north = plot["north_deg"]
    # plate
    c.rect(plate, fill="#ffffff", stroke="#111", sw=3.0)
    if show_zones:
        zx, zy, zw, zh = plate["x"], plate["y"], plate["w"] / 3, plate["h"] / 3
        for i in range(1, 3):
            c.line(zx + i * zw, zy, zx + i * zw, zy + plate["h"], "#bbb", 0.6, dash="4,4")
            c.line(zx, zy + i * zh, zx + plate["w"], zy + i * zh, "#bbb", 0.6, dash="4,4")
    # corridor & core
    if f.get("corridor"):
        c.rect(f["corridor"], fill=ROOM_FILL["corridor"], stroke="#333", sw=1.0)
        cr = f["corridor"]
        c.text(cr["x"] + cr["w"] / 2, cr["y"] + cr["h"] / 2, "CORRIDOR", 9, fill="#666")
    for name, r in f.get("core", {}).items():
        if name == "block":
            c.rect(r, fill=ROOM_FILL["core"], stroke="#333", sw=1.2)
    for name, r in f.get("core", {}).items():
        if name != "block":
            c.rect(r, fill="#eeeeee", stroke="#333", sw=1.0)
            c.text(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2, name.upper().replace("LIFT", "LIFT "), 8, fill="#444")
            if name == "stair":
                n = 9
                for i in range(1, n):
                    if r["w"] >= r["h"]:
                        c.line(r["x"] + i * r["w"] / n, r["y"], r["x"] + i * r["w"] / n, r["y"] + r["h"], "#999", 0.5)
                    else:
                        c.line(r["x"], r["y"] + i * r["h"] / n, r["x"] + r["w"], r["y"] + i * r["h"] / n, "#999", 0.5)
    for er in f.get("extra_rooms", []):
        r = er["rect"]
        c.rect(r, fill=ROOM_FILL.get(er["type"], "#f4f4f4"), stroke="#333", sw=1.0)
        c.text(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2 + 0.8, er["label"], 9, weight="bold")
        c.text(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2 - 0.9, f"{U.ftin(r['w'])} x {U.ftin(r['h'])}", 7.5, fill="#555")
    for b in f.get("parking", []):
        c.rect(b, fill=ROOM_FILL["parking"], stroke="#888", sw=0.6)
    # units
    for u in f["units"]:
        c.rect(u["rect"], fill="none", stroke="#111", sw=2.2)
        for r in u["rooms"]:
            cl = r["clear"]
            c.rect(cl, fill=ROOM_FILL.get(r["type"], "#f4f4f4"), stroke="#222", sw=1.4, extra=f'data-room="{escape(r["key"])}" data-unit="{escape(u["label"])}"')
            if r["type"] == "stair":
                n = 10
                for i in range(1, n):
                    if cl["w"] >= cl["h"]:
                        c.line(cl["x"] + i * cl["w"] / n, cl["y"], cl["x"] + i * cl["w"] / n, cl["y"] + cl["h"], "#999", 0.5)
                    else:
                        c.line(cl["x"], cl["y"] + i * cl["h"] / n, cl["x"] + cl["w"], cl["y"] + i * cl["h"] / n, "#999", 0.5)
            big = min(cl["w"], cl["h"]) * c.s > 26
            cx, cy = cl["x"] + cl["w"] / 2, cl["y"] + cl["h"] / 2
            if big:
                c.text(cx, cy + 1.1, r["label"], 9, weight="bold")
                c.text(cx, cy - 0.2, f"{U.ftin(cl['w'])} x {U.ftin(cl['h'])}", 7.5, fill="#444")
                c.text(cx, cy - 1.4, f"{cl['w']*cl['h']:.0f} sq ft ({U.sqft_to_sqm(cl['w']*cl['h']):.1f} m²)", 6.5, fill="#666")
                if show_zones:
                    c.text(cl["x"] + 0.8, cl["y"] + cl["h"] - 0.8, r["zone"], 7, anchor="start", fill="#7a4")
            elif min(cl["w"], cl["h"]) * c.s > 12:
                c.text(cx, cy, r["label"][:14], 6.5, weight="bold")
            if r.get("issues"):
                px, py = c.P(cl["x"] + cl["w"] - 0.8, cl["y"] + cl["h"] - 0.8)
                c.parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="#d97706"><title>{escape("; ".join(r["issues"]))}</title></circle>')
        for r in u["rooms"]:
            if r.get("door"):
                _door(c, r["door"])
            for wd in r.get("windows", []):
                _window(c, wd)
        ur = u["rect"]
        c.text(ur["x"] + ur["w"] / 2, ur["y"] + ur["h"] + 1.2, f"{u['label']} · {u['type'].upper()} · carpet {u['carpet_sqft']:.0f} sq ft", 8, fill="#333")
    for sh in f.get("shafts", []):
        r = sh["rect"]
        c.rect(r, fill="#ffffff", stroke="#2a7ab8", sw=0.8)
        c.line(r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"], "#2a7ab8", 0.6)
        c.line(r["x"], r["y"] + r["h"], r["x"] + r["w"], r["y"], "#2a7ab8", 0.6)
    # grid + columns
    cols = f.get("columns", {})
    if show_grid and cols:
        for x, lab in zip(cols["grid_x"], cols["labels_x"]):
            c.line(x, plate["y"] - 3, x, plate["y"] + plate["h"] + 3, "#888", 0.5, dash="6,3")
            px, py = c.P(x, plate["y"] + plate["h"] + 4.2)
            c.parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="7" fill="#fff" stroke="#555" stroke-width="0.8"/>')
            c.text(x, plate["y"] + plate["h"] + 4.2, lab, 8)
        for y, lab in zip(cols["grid_y"], cols["labels_y"]):
            c.line(plate["x"] - 3, y, plate["x"] + plate["w"] + 3, y, "#888", 0.5, dash="6,3")
            px, py = c.P(plate["x"] - 4.2, y)
            c.parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="7" fill="#fff" stroke="#555" stroke-width="0.8"/>')
            c.text(plate["x"] - 4.2, y, lab, 8)
        for col in cols["columns"]:
            c.rect({"x": col["x"] - col["w"] / 2, "y": col["y"] - col["d"] / 2, "w": col["w"], "h": col["d"]}, fill="#222", stroke="#000", sw=0.5)
    # overall dimensions
    y0 = plate["y"] - 5.5
    c.line(plate["x"], y0, plate["x"] + plate["w"], y0, "#333", 0.8)
    c.line(plate["x"], y0 - 1, plate["x"], y0 + 1, "#333", 0.8); c.line(plate["x"] + plate["w"], y0 - 1, plate["x"] + plate["w"], y0 + 1, "#333", 0.8)
    c.text(plate["x"] + plate["w"] / 2, y0 - 1.3, U.length_label(plate["w"]), 8)
    x0 = plate["x"] + plate["w"] + 5.5
    c.line(x0, plate["y"], x0, plate["y"] + plate["h"], "#333", 0.8)
    c.line(x0 - 1, plate["y"], x0 + 1, plate["y"], "#333", 0.8); c.line(x0 - 1, plate["y"] + plate["h"], x0 + 1, plate["y"] + plate["h"], "#333", 0.8)
    c.text(x0 + 1.3, plate["y"] + plate["h"] / 2, U.length_label(plate["h"]), 8, rotate=-90)
    # road label on the front side
    fs = option.get("front_side", "bottom")
    rx, ry = {"bottom": (plate["x"] + plate["w"] / 2, plate["y"] - 7.3), "top": (plate["x"] + plate["w"] / 2, plate["y"] + plate["h"] + 7.3),
              "left": (plate["x"] - 7.3, plate["y"] + plate["h"] / 2), "right": (plate["x"] + plate["w"] + 7.3, plate["y"] + plate["h"] / 2)}[fs]
    c.text(rx, ry, "ROAD / ENTRY SIDE", 8, fill="#a33", weight="bold", rotate=0 if fs in ("top", "bottom") else -90)
    north_arrow(c, bounds[2] - 6, bounds[3] - 5, north)
    c.text(bounds[0] + 1, bounds[3] - 2, f"{option.get('name', '')} · {f['name']}" + (f" (x{f['repeat']})" if f.get("repeat", 1) > 1 else ""), 11, anchor="start", weight="bold")
    return c.render(f"{option.get('name', '')} {f['name']}")


def render_site(option: dict, width_px: int = 900) -> str:
    plot = option["plot"]
    poly = plot["polygon"]
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    pad = 10.0
    c = Canvas((min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad + 4), width_px)
    c.poly(poly, fill="#f9f7ee", stroke="#111", sw=2.0)
    c.poly(plot["envelope"], fill="none", stroke="#c33", sw=1.0, dash="6,4")
    fp = option["footprint"]
    c.rect(fp, fill="#d9d9d9", stroke="#111", sw=1.8)
    c.text(fp["x"] + fp["w"] / 2, fp["y"] + fp["h"] / 2, f"BUILDING {U.ftin(fp['w'])} x {U.ftin(fp['h'])}", 10, weight="bold")
    site = option["site"]
    if site.get("driveway"):
        c.poly(site["driveway"], fill="#e8e8e8", stroke="#777", sw=0.8)
    for b in site.get("parking", []):
        c.rect(b, fill="#ffffff", stroke="#666", sw=0.7)
        c.text(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2, "P", 8, fill="#666")
    for sv in site.get("services", []):
        if not sv.get("placed"):
            continue
        r = sv["rect"]
        fill = "#cfe8ff" if sv["name"] in ("ug_tank", "septic_tank", "rwh_pit", "fire_tank", "stp", "borewell") else "#ffe0b3" if sv["name"] == "oht" else "#e8dcc8"
        c.rect(r, fill=fill, stroke="#444", sw=0.8, dash="3,2" if sv["name"] in ("ug_tank", "septic_tank", "rwh_pit", "fire_tank", "stp") else None)
        c.text(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2, sv["label"], 7)
    g = site["gate"]
    px, py = c.P(g["x"], g["y"])
    c.parts.append(f'<rect x="{px-6:.1f}" y="{py-6:.1f}" width="12" height="12" fill="#c33"/>')
    c.text(g["x"], g["y"] - 3.5, "GATE", 8, fill="#c33", weight="bold")
    for e in plot["edges"]:
        mx, my = (e["a"][0] + e["b"][0]) / 2, (e["a"][1] + e["b"][1]) / 2
        dx, dy = e["b"][0] - e["a"][0], e["b"][1] - e["a"][1]
        L = math.hypot(dx, dy) or 1
        nx, ny = dy / L, -dx / L
        lab = f"{e['compass']} · {e['role']} · setback {U.ftin(e['setback_ft'])}"
        if e.get("road_width_m"):
            lab = f"ROAD {e['road_width_m']} m · " + lab
        else:
            lab = f"{e['adjoining']} · " + lab
        ang = -math.degrees(math.atan2(dy, dx))
        if ang > 90 or ang < -90:
            ang += 180
        c.text(mx + nx * 4.5, my + ny * 4.5, lab, 8, fill="#333", rotate=ang)
        c.text(mx - nx * 2.5, my - ny * 2.5, U.length_label(e["length_ft"]), 7.5, fill="#555", rotate=ang)
    north_arrow(c, max(xs) + pad - 6, max(ys) + pad - 1, plot["north_deg"])
    c.text(min(xs) - pad + 1, max(ys) + pad + 1, f"SITE PLAN · plot {plot['area_label']}", 11, anchor="start", weight="bold")
    return c.render("Site plan")
