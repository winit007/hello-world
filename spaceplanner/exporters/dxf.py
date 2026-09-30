"""Layered DXF export (AutoCAD R2010) via ezdxf. Layers: WALLS, DOORS, WINDOWS, TEXT, DIMS, GRID, COLUMNS, SETBACK, PLOT, SERVICES, PARKING, SHAFT."""
from __future__ import annotations
import math

import ezdxf
from ezdxf import units as ezunits

from .. import units as U
from ..config import get


def _scale(cfg: dict) -> float:
    u = get(cfg, "export.dxf_units", "inches")
    return {"inches": 12.0, "feet": 1.0, "mm": 304.8}.get(u, 12.0)


def write_dxf(option: dict, path: str, cfg: dict) -> str:
    doc = ezdxf.new("R2010", setup=True)
    u = get(cfg, "export.dxf_units", "inches")
    doc.units = {"inches": ezunits.IN, "feet": ezunits.FT, "mm": ezunits.MM}.get(u, ezunits.IN)
    S = _scale(cfg)
    for name, spec in (get(cfg, "export.layers") or {}).items():
        attrs = {"color": int(spec.get("color", 7))}
        if spec.get("linetype"):
            attrs["linetype"] = spec["linetype"]
        doc.layers.add(name, **attrs)
    msp = doc.modelspace()
    th = float(get(cfg, "export.text_height_in", 6)) / 12 * S
    dth = float(get(cfg, "export.dim_text_height_in", 4)) / 12 * S
    doc.dimstyles.get("EZDXF").dxf.dimtxt = dth
    doc.dimstyles.get("EZDXF").dxf.dimasz = dth * 0.6

    def pt(x, y, ox=0.0, oy=0.0):
        return ((x + ox) * S, (y + oy) * S)

    def rect(r, layer, ox=0.0, oy=0.0, close=True):
        pts = [pt(r["x"], r["y"], ox, oy), pt(r["x"] + r["w"], r["y"], ox, oy), pt(r["x"] + r["w"], r["y"] + r["h"], ox, oy), pt(r["x"], r["y"] + r["h"], ox, oy)]
        msp.add_lwpolyline(pts, close=close, dxfattribs={"layer": layer})

    def text(x, y, s, layer="TEXT", h=None, ox=0.0, oy=0.0, align="MIDDLE_CENTER", rot=0.0):
        t = msp.add_text(s, dxfattribs={"layer": layer, "height": h or th, "rotation": rot})
        t.set_placement(pt(x, y, ox, oy), align=ezdxf.enums.TextEntityAlignment[align])

    # ---- site plan at origin
    plot = option["plot"]
    msp.add_lwpolyline([pt(x, y) for x, y in plot["polygon"]], close=True, dxfattribs={"layer": "PLOT"})
    msp.add_lwpolyline([pt(x, y) for x, y in plot["envelope"]], close=True, dxfattribs={"layer": "SETBACK"})
    fp = option["footprint"]
    rect(fp, "WALLS")
    text(fp["x"] + fp["w"] / 2, fp["y"] + fp["h"] / 2, f"BUILDING {U.ftin(fp['w'])} x {U.ftin(fp['h'])}")
    for e in plot["edges"]:
        mx, my = (e["a"][0] + e["b"][0]) / 2, (e["a"][1] + e["b"][1]) / 2
        dx, dy = e["b"][0] - e["a"][0], e["b"][1] - e["a"][1]
        L = math.hypot(dx, dy) or 1
        nx, ny = dy / L, -dx / L
        lab = (f"ROAD {e['road_width_m']} m - " if e.get("road_width_m") else f"{e['adjoining']} - ") + f"{e['compass']} {e['role']} setback {U.ftin(e['setback_ft'])}"
        ang = math.degrees(math.atan2(dy, dx))
        if ang > 90 or ang < -90:
            ang += 180
        text(mx + nx * 4, my + ny * 4, lab, h=th * 0.8, rot=ang)
        dim = msp.add_aligned_dim(p1=pt(*e["a"]), p2=pt(*e["b"]), distance=-8 * S if False else 8 * S, dimstyle="EZDXF", override={"dimtxt": dth}, dxfattribs={"layer": "DIMS"})
        dim.render()
    site = option["site"]
    if site.get("driveway"):
        msp.add_lwpolyline([pt(x, y) for x, y in site["driveway"]], close=True, dxfattribs={"layer": "PARKING"})
    for b in site.get("parking", []):
        rect(b, "PARKING")
    for sv in site.get("services", []):
        if sv.get("placed"):
            rect(sv["rect"], "SERVICES")
            text(sv["rect"]["x"] + sv["rect"]["w"] / 2, sv["rect"]["y"] + sv["rect"]["h"] / 2, sv["label"], h=th * 0.7)
    g = site["gate"]
    msp.add_circle(pt(g["x"], g["y"]), 1.5 * S, dxfattribs={"layer": "SERVICES"})
    text(g["x"], g["y"] - 3, "GATE", h=th * 0.8)
    _north_arrow(msp, pt(max(p[0] for p in plot["polygon"]) + 6, max(p[1] for p in plot["polygon"]) + 4), plot["north_deg"], S, th)
    text(min(p[0] for p in plot["polygon"]), max(p[1] for p in plot["polygon"]) + 8, f"SITE PLAN - {option.get('name', '')} - plot {plot['area_label']}", h=th * 1.3, align="BOTTOM_LEFT")

    # ---- floors side by side to the right of the site
    ox = max(p[0] for p in plot["polygon"]) + 40.0
    for f in option["floors"]:
        plate = f["plate"]
        shift_x = ox - plate["x"]
        shift_y = -plate["y"] + min(p[1] for p in plot["polygon"])
        _floor(msp, f, option, shift_x, shift_y, S, th, dth, rect, text, pt)
        ox += plate["w"] + 40.0
    doc.saveas(path)
    return path


def _north_arrow(msp, origin, north_deg, S, th):
    ang = math.radians(north_deg)
    dx, dy = math.sin(ang), math.cos(ang)
    x0, y0 = origin
    L = 6 * S
    msp.add_line((x0 - dx * L, y0 - dy * L), (x0 + dx * L, y0 + dy * L), dxfattribs={"layer": "TEXT"})
    tip = (x0 + dx * L, y0 + dy * L)
    perp = (-dy, dx)
    msp.add_solid([tip, (tip[0] - dx * 2 * S + perp[0] * S, tip[1] - dy * 2 * S + perp[1] * S), (tip[0] - dx * 2 * S - perp[0] * S, tip[1] - dy * 2 * S - perp[1] * S)], dxfattribs={"layer": "TEXT"})
    t = msp.add_text("N", dxfattribs={"layer": "TEXT", "height": th * 1.2})
    t.set_placement((tip[0] + dx * 2.5 * S, tip[1] + dy * 2.5 * S), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)


def _floor(msp, f, option, ox, oy, S, th, dth, rect, text, pt):
    plate = f["plate"]
    rect(plate, "WALLS", ox, oy)
    text(plate["x"], plate["y"] + plate["h"] + 6, f"{f['name']}" + (f" (x{f['repeat']})" if f.get("repeat", 1) > 1 else ""), h=th * 1.3, ox=ox, oy=oy, align="BOTTOM_LEFT")
    if f.get("corridor"):
        rect(f["corridor"], "WALLS", ox, oy)
        text(f["corridor"]["x"] + f["corridor"]["w"] / 2, f["corridor"]["y"] + f["corridor"]["h"] / 2, "CORRIDOR", h=th * 0.8, ox=ox, oy=oy)
    for name, r in f.get("core", {}).items():
        if name == "block":
            continue
        rect(r, "WALLS", ox, oy)
        text(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2, name.upper(), h=th * 0.8, ox=ox, oy=oy)
        if name == "stair":
            n = 9
            for i in range(1, n):
                if r["w"] >= r["h"]:
                    msp.add_line(pt(r["x"] + i * r["w"] / n, r["y"], ox, oy), pt(r["x"] + i * r["w"] / n, r["y"] + r["h"], ox, oy), dxfattribs={"layer": "WALLS"})
                else:
                    msp.add_line(pt(r["x"], r["y"] + i * r["h"] / n, ox, oy), pt(r["x"] + r["w"], r["y"] + i * r["h"] / n, ox, oy), dxfattribs={"layer": "WALLS"})
    for er in f.get("extra_rooms", []):
        rect(er["rect"], "WALLS", ox, oy)
        r = er["rect"]
        text(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2 + 1, er["label"], ox=ox, oy=oy)
        text(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2 - 1, f"{U.ftin(r['w'])} x {U.ftin(r['h'])}", h=th * 0.75, ox=ox, oy=oy)
    for b in f.get("parking", []):
        rect(b, "PARKING", ox, oy)
    for u in f["units"]:
        rect(u["rect"], "WALLS", ox, oy)
        for r in u["rooms"]:
            cl = r["clear"]
            rect(cl, "WALLS", ox, oy)
            text(cl["x"] + cl["w"] / 2, cl["y"] + cl["h"] / 2 + 1.0, r["label"].upper(), ox=ox, oy=oy)
            text(cl["x"] + cl["w"] / 2, cl["y"] + cl["h"] / 2 - 0.6, f"{U.ftin(cl['w'])} x {U.ftin(cl['h'])}", h=th * 0.75, ox=ox, oy=oy)
            text(cl["x"] + cl["w"] / 2, cl["y"] + cl["h"] / 2 - 1.8, f"({U.ft_to_m(cl['w']):.2f} x {U.ft_to_m(cl['h']):.2f} m)", h=th * 0.6, ox=ox, oy=oy)
            if r.get("door") and not r["door"].get("opening"):
                d = r["door"]
                x1, y1, x2, y2 = d["seg"]
                w = d["w"]
                horiz = abs(y2 - y1) < 1e-6
                if d.get("sliding"):
                    msp.add_line(pt(x1, y1, ox, oy), pt(x2, y2, ox, oy), dxfattribs={"layer": "DOORS"})
                else:
                    side = d["side"]
                    if horiz:
                        into = -1 if side == "top" else 1
                        msp.add_line(pt(x1, y1, ox, oy), pt(x1, y1 + into * w, ox, oy), dxfattribs={"layer": "DOORS"})
                        msp.add_arc(pt(x1, y1, ox, oy), w * S, 0 if into > 0 else 270, 90 if into > 0 else 360, dxfattribs={"layer": "DOORS"})
                    else:
                        into = -1 if side == "right" else 1
                        msp.add_line(pt(x1, y1, ox, oy), pt(x1 + into * w, y1, ox, oy), dxfattribs={"layer": "DOORS"})
                        msp.add_arc(pt(x1, y1, ox, oy), w * S, 0 if into > 0 else 90, 90 if into > 0 else 180, dxfattribs={"layer": "DOORS"})
                text((x1 + x2) / 2, (y1 + y2) / 2, d["tag"], h=th * 0.6, ox=ox, oy=oy)
            elif r.get("door"):
                x1, y1, x2, y2 = r["door"]["seg"]
                msp.add_line(pt(x1, y1, ox, oy), pt(x2, y2, ox, oy), dxfattribs={"layer": "DOORS", "linetype": "DASHED"})
            for wd in r.get("windows", []):
                x1, y1, x2, y2 = wd["seg"]
                if abs(y2 - y1) < 1e-6:
                    for off in (-0.2, 0.2):
                        msp.add_line(pt(x1, y1 + off, ox, oy), pt(x2, y2 + off, ox, oy), dxfattribs={"layer": "WINDOWS"})
                else:
                    for off in (-0.2, 0.2):
                        msp.add_line(pt(x1 + off, y1, ox, oy), pt(x2 + off, y2, ox, oy), dxfattribs={"layer": "WINDOWS"})
                text((x1 + x2) / 2, (y1 + y2) / 2, wd["tag"], h=th * 0.6, ox=ox, oy=oy)
        ur = u["rect"]
        text(ur["x"] + ur["w"] / 2, ur["y"] - 1.5, f"{u['label']} {u['type'].upper()} carpet {u['carpet_sqft']:.0f} sq ft", h=th * 0.8, ox=ox, oy=oy)
    for sh in f.get("shafts", []):
        rect(sh["rect"], "SHAFT", ox, oy)
    cols = f.get("columns", {})
    if cols:
        for x, lab in zip(cols["grid_x"], cols["labels_x"]):
            msp.add_line(pt(x, plate["y"] - 4, ox, oy), pt(x, plate["y"] + plate["h"] + 4, ox, oy), dxfattribs={"layer": "GRID", "linetype": "CENTER"})
            msp.add_circle(pt(x, plate["y"] + plate["h"] + 5.5, ox, oy), 1.2 * S, dxfattribs={"layer": "GRID"})
            text(x, plate["y"] + plate["h"] + 5.5, lab, layer="GRID", h=th * 0.8, ox=ox, oy=oy)
        for y, lab in zip(cols["grid_y"], cols["labels_y"]):
            msp.add_line(pt(plate["x"] - 4, y, ox, oy), pt(plate["x"] + plate["w"] + 4, y, ox, oy), dxfattribs={"layer": "GRID", "linetype": "CENTER"})
            msp.add_circle(pt(plate["x"] - 5.5, y, ox, oy), 1.2 * S, dxfattribs={"layer": "GRID"})
            text(plate["x"] - 5.5, y, lab, layer="GRID", h=th * 0.8, ox=ox, oy=oy)
        for col in cols["columns"]:
            r = {"x": col["x"] - col["w"] / 2, "y": col["y"] - col["d"] / 2, "w": col["w"], "h": col["d"]}
            pts = [pt(r["x"], r["y"], ox, oy), pt(r["x"] + r["w"], r["y"], ox, oy), pt(r["x"] + r["w"], r["y"] + r["h"], ox, oy), pt(r["x"], r["y"] + r["h"], ox, oy)]
            msp.add_solid([pts[0], pts[1], pts[3], pts[2]], dxfattribs={"layer": "COLUMNS"})
        # grid dimensions
        for a, b in zip(cols["grid_x"], cols["grid_x"][1:]):
            dim = msp.add_linear_dim(base=pt(a, plate["y"] - 7, ox, oy), p1=pt(a, plate["y"], ox, oy), p2=pt(b, plate["y"], ox, oy), dimstyle="EZDXF", dxfattribs={"layer": "DIMS"})
            dim.render()
        for a, b in zip(cols["grid_y"], cols["grid_y"][1:]):
            dim = msp.add_linear_dim(base=pt(plate["x"] + plate["w"] + 7, a, ox, oy), p1=pt(plate["x"] + plate["w"], a, ox, oy), p2=pt(plate["x"] + plate["w"], b, ox, oy), angle=90, dimstyle="EZDXF", dxfattribs={"layer": "DIMS"})
            dim.render()
    dim = msp.add_linear_dim(base=pt(plate["x"], plate["y"] - 10, ox, oy), p1=pt(plate["x"], plate["y"], ox, oy), p2=pt(plate["x"] + plate["w"], plate["y"], ox, oy), dimstyle="EZDXF", dxfattribs={"layer": "DIMS"})
    dim.render()
    dim = msp.add_linear_dim(base=pt(plate["x"] + plate["w"] + 10, plate["y"], ox, oy), p1=pt(plate["x"] + plate["w"], plate["y"], ox, oy), p2=pt(plate["x"] + plate["w"], plate["y"] + plate["h"], ox, oy), angle=90, dimstyle="EZDXF", dxfattribs={"layer": "DIMS"})
    dim.render()
    _north_arrow(msp, pt(plate["x"] + plate["w"] + 6, plate["y"] + plate["h"] + 4, ox, oy), option["plot"]["north_deg"], S, th)
