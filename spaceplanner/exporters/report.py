"""Area statement, schedules, Vastu report and warnings as Markdown and HTML."""
from __future__ import annotations
from html import escape

from .. import units as U


def _tbl(headers: list[str], rows: list[list], md: bool) -> str:
    if md:
        out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
        out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
        return "\n".join(out) + "\n"
    h = "".join(f"<th>{escape(str(c))}</th>" for c in headers)
    b = "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"


def _h(level: int, s: str, md: bool) -> str:
    return f"{'#' * level} {s}\n\n" if md else f"<h{level}>{escape(s)}</h{level}>"


def _p(s: str, md: bool) -> str:
    return s + "\n\n" if md else f"<p>{escape(s)}</p>"


def _ul(items: list[str], md: bool) -> str:
    if not items:
        return _p("None.", md)
    return "".join(f"- {i}\n" for i in items) + "\n" if md else "<ul>" + "".join(f"<li>{escape(i)}</li>" for i in items) + "</ul>"


def build(result: dict, option: dict, md: bool = True) -> str:
    A = option["areas"]; R = option["regulatory"]; P = option["plot"]
    s = _h(1, f"Space Planner report - {option['name']}", md)
    s += _p(f"Project: {result.get('project_type', '').replace('_', ' ').title()} · goal {result.get('goal', '')} · Vastu mode {result.get('vastu_mode', '')}. {option['summary']}.", md)
    s += _p(f"Overall score {option['score']} / 100 " + ", ".join(f"{k.replace('_', ' ')} {v*100:.0f}%" for k, v in option["scores"].items()) + ".", md)

    s += _h(2, "Plot and regulation", md)
    rows = [["Plot area", P["area_label"]],
            ["North", f"{P['north_deg']:.0f}° clockwise from the plan's up direction"],
            ["Buildable envelope after setbacks", U.area_label(P["envelope_area_sqft"])],
            ["Building footprint", f"{U.area_label(A['footprint_sqft'])} = {R['coverage_pct']}% coverage (max {R['coverage_pct_max']}%) {'OK' if R['coverage_ok'] else 'EXCEEDS'}"],
            ["FAR", f"used {R['far_used']} of permitted {R['far_permitted']} (+{R['far_purchasable']} purchasable) {'OK' if R['far_ok'] else 'EXCEEDS'}"],
            ["Floors / height", f"{R['floors']} floors{' + stilt' if R.get('stilt') else ''}, {R['height_label']} (max {U.ftin(R['max_height_ft'])}) {'OK' if R['height_ok'] else 'EXCEEDS'}"],
            ["High-rise fire norms", "apply (second stair, fire tank, refuge)" if R["high_rise"] else "not triggered"],
            ["Lift", "required" if R["lift_required"] else "not mandatory"],
            ["Parking", f"{A['parking']['ecs_provided']} ECS provided ({A['parking']['surface_bays']} surface + {A['parking']['stilt_bays']} stilt) vs {A['parking']['ecs_required']} required"],
            ["Bye-law source", f"{R.get('source', '')} - VERIFY WITH AUTHORITY"]]
    s += _tbl(["Item", "Value"], rows, md)
    s += _h(3, "Setbacks", md)
    s += _tbl(["Edge", "Faces", "Role", "Abutting", "Length", "Setback", "Source"],
              [[e["index"], e["compass"], e["role"], f"road {e['road_width_m']} m" if e.get("road_width_m") else e["adjoining"], U.length_label(e["length_ft"]),
                e["setback_label"], e["setback_source"]] for e in P["edges"]], md)

    s += _h(2, "Area statement", md)
    s += _tbl(["Floor", "Unit", "Type", "Count", "RERA carpet", "Balcony", "Built-up", "Super built-up", "Vastu", "Score"],
              [[u["floor"], u["label"], u["type"].upper(), u["count"], U.area_label(u["carpet_sqft"]), f"{u['balcony_sqft']:.0f} sq ft", U.area_label(u["builtup_sqft"]),
                U.area_label(u["super_builtup_sqft"]), f"{(u['vastu'] or 0)*100:.0f}%" if u.get("vastu") is not None else "-", u["score"] if not u["error"] else "NOT PLANNED"]
               for u in A["units"]], md)
    s += _tbl(["Total", "Value"], [
        ["Total built-up (all floors)", U.area_label(A["total_builtup_sqft"])],
        ["FAR-counted area", U.area_label(A["far_area_sqft"])],
        ["Total RERA carpet", U.area_label(A["total_carpet_sqft"])],
        ["Total balcony", U.area_label(A["total_balcony_sqft"])],
        ["Total super built-up (x " + str(A["super_builtup_factor"]) + ")", U.area_label(A["total_super_builtup_sqft"])],
        ["Common area", U.area_label(A["common_sqft"])],
        ["Efficiency (carpet / super built-up)", f"{A['efficiency']*100:.0f}%"],
    ], md)
    s += _p("RERA carpet = net usable area inside the flat's external walls including internal partitions, excluding external walls, shafts and exclusive balcony.", md)

    for f in option["floors"]:
        s += _h(2, f"{f['name']}" + (f" (repeated x{f['repeat']})" if f.get("repeat", 1) > 1 else ""), md)
        for u in f["units"]:
            s += _h(3, f"{u['label']} - {u['type'].upper()}" + (f" [{u['error']}]" if u["error"] else ""), md)
            if u["rooms"]:
                s += _tbl(["Room", "Size (ft-in)", "Size (m)", "Area", "Zone", "Windows", "Notes"],
                          [[r["label"], r["size_label"], f"{U.ft_to_m(r['w']):.2f} x {U.ft_to_m(r['h']):.2f}", f"{r['area_sqft']:.0f} sq ft", r["zone"],
                            ", ".join(f"{w['tag']} {U.ftin(w['w'])}x{U.ftin(w['h'])}" for w in r.get("windows", [])) or "-", "; ".join(r.get("issues", [])) or ""]
                           for r in u["rooms"]], md)
            if u.get("relaxed"):
                s += _ul(u["relaxed"], md)
        if f.get("extra_rooms"):
            s += _tbl(["Space", "Size"], [[e["label"], f"{U.ftin(e['rect']['w'])} x {U.ftin(e['rect']['h'])}"] for e in f["extra_rooms"]], md)
        c = f["columns"]
        s += _p(f"Column grid: {len(c['grid_x'])} x {len(c['grid_y'])} lines, {len(c['columns'])} columns of {c['size_in'][0]}\" x {c['size_in'][1]}\", regularity {c['regularity']*100:.0f}%.", md)

    s += _h(2, "Door and window schedule", md)
    sc = option["schedule"]
    s += _tbl(["Tag", "Size", "Count", "Rooms"], [[e["tag"], f"{U.ftin(e['w'])} x {U.ftin(e['h'])}" + (" sliding" if e.get("sliding") else ""), e["count"], ", ".join(e["rooms"][:6])]
                                                 for e in sc.get("doors", {}).values()], md)
    s += _tbl(["Tag", "Size", "Sill", "Count", "Rooms"], [[e["tag"], f"{U.ftin(e['w'])} x {U.ftin(e['h'])}" + (" ventilator" if e.get("kind") == "ventilator" else ""), U.ftin(e["sill"]), e["count"], ", ".join(e["rooms"][:6])]
                                                         for e in sc.get("windows", {}).values()], md)

    s += _h(2, "Vastu compliance", md)
    vs = option["vastu"]["summary"]
    s += _p(f"Score {vs['score']}% - {vs['pass']} pass, {vs['partial']} partial, {vs['fail']} fail (mode: {option['vastu']['mode']}).", md)
    s += _tbl(["Level", "Rule", "Subject", "Zone", "Expected", "Status", "Mode"],
              [[r["level"], r["rule"], r["subject"], r["zone"], r["expected"], r["status"].upper(), r["mode"]] for r in option["vastu"]["rows"]], md)

    s += _h(2, "Warnings and items to verify", md)
    s += _ul(option["warnings"] + result.get("warnings", []), md)
    if not md:
        s = ("<!doctype html><html><head><meta charset='utf-8'><title>Space Planner report</title><style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222}"
             "table{border-collapse:collapse;margin:8px 0 16px;font-size:13px}th,td{border:1px solid #ccc;padding:4px 8px;text-align:left}th{background:#f3f3f3}"
             "h1{font-size:22px}h2{font-size:17px;margin-top:26px}h3{font-size:14px}</style></head><body>" + s + "</body></html>")
    return s


def render_markdown(result: dict, option: dict) -> str:
    return build(result, option, md=True)


def render_html(result: dict, option: dict) -> str:
    return build(result, option, md=False)
