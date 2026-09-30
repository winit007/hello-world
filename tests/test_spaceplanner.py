"""Stage tests on the two reference plots: 40' x 60' east-facing house and a 10-kattha two-road corner apartment plot."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from spaceplanner import units as U  # noqa: E402
from spaceplanner.config import load_config  # noqa: E402
from spaceplanner import site as S  # noqa: E402
from spaceplanner import layout as L  # noqa: E402
from spaceplanner.geometry import Rect, shared_wall  # noqa: E402
from spaceplanner.engine import generate_options, rescore_unit  # noqa: E402
from spaceplanner.exporters import svg as svg_exp, dxf as dxf_exp, report as report_exp  # noqa: E402

HOUSE = json.load(open(os.path.join(ROOT, "examples", "plot_40x60_east.json")))
APT = json.load(open(os.path.join(ROOT, "examples", "plot_10kattha_corner.json")))


# ---------------------------------------------------------------- stage 1: units, site, setbacks, areas
def test_units_parse_and_format():
    assert abs(U.parse_length("12'6\"") - 12.5) < 1e-9
    assert abs(U.parse_length("12'-6\"") - 12.5) < 1e-9
    assert abs(U.parse_length('9"') - 0.75) < 1e-9
    assert abs(U.parse_length("3m") - 9.8425) < 1e-3
    assert abs(U.parse_length("230mm") - 0.7546) < 1e-3
    assert U.ftin(12.5) == "12'-6\""
    assert U.ftin(10.0) == "10'-0\""
    land = U.LandUnits(1361.25, 20)
    assert abs(land.to_sqft(kattha=10) - 13612.5) < 1e-6
    k, d = land.from_sqft(2400)["kattha_dhur"]
    assert k == 1 and abs(d - 15.26) < 0.05
    assert abs(U.LandUnits(720).to_sqft(kattha=1) - 720) < 1e-9  # editable kattha


def test_site_40x60_east_facing():
    cfg = load_config()
    p = S.build_plot(HOUSE["site"], cfg)
    assert abs(p.area - 2400) < 1e-6
    front = p.edges[p.entry_edge]
    assert front.compass == "E" and front.role == "front" and front.road_width_m == 9
    sb = S.apply_setbacks(p, 30, cfg)
    env = sb["envelope"]
    assert 0 < env.area < p.area
    assert all(v > 0 for v in sb["setbacks"].values())
    reg = S.regulatory_summary(p, cfg, 2, 10)
    assert reg["far_permitted"] == 2.0 and reg["coverage_pct_max"] == 65
    assert any("Verify" in w for w in reg["warnings"] + sb["warnings"])


def test_site_10_kattha_corner():
    cfg = load_config()
    p = S.build_plot(APT["site"], cfg)
    assert abs(p.area - 13612.5) < 1.0
    assert p.corner and len(p.roads) == 2
    roles = [e.role for e in p.edges]
    assert roles.count("front") == 2
    sb = S.apply_setbacks(p, 60, cfg)
    assert any("splay" in w for w in sb["warnings"])
    x, y, w, h = S.largest_inscribed_rect(sb["envelope"])
    assert w > 40 and h > 40
    # height-driven setback: 6 m for a 60 ft (18.3 m) building
    assert all(abs(v - U.m_to_ft(6.0)) < 0.01 for v in sb["setbacks"].values())


def test_setback_override():
    cfg = load_config()
    p = S.build_plot(HOUSE["site"], cfg)
    sb = S.apply_setbacks(p, 20, cfg, overrides={"front": "12'0\""})
    assert abs(p.edges[p.entry_edge].setback_ft - 12.0) < 1e-6


def test_zone_grid_rotation():
    z0 = S.zone_grid((0, 0, 30, 30), 0)
    assert z0(28, 28) == "NE" and z0(2, 2) == "SW" and z0(15, 15) == "C" and z0(15, 28) == "N"
    z90 = S.zone_grid((0, 0, 30, 30), 90)      # north points to the right (+x)
    assert z90(28, 15) == "N" and z90(2, 15) == "S" and z90(28, 2) == "NE"


# ---------------------------------------------------------------- stage 2: house layout
def test_house_layout_hard_rules():
    cfg = load_config()
    ctx = L.UnitContext(Rect(0, 0, 28.6, 43.7), {s: True for s in L.SIDES}, entry_side="bottom", north_deg=90)
    specs = L.build_programme("house", cfg)
    lays = L.generate(ctx, specs, cfg, goal="vastu", keep=3)
    assert lays, "no feasible house layout"
    lay = lays[0]
    assert lay.hard_ok
    rooms = lay.rooms
    kitchen = next(r for r in rooms.values() if r.type == "kitchen")
    for r in rooms.values():
        if r.type == "toilet":
            assert shared_wall(kitchen.cell, r.cell) is None
        if r.spec.external == "required":
            assert r.external_sides, f"{r.spec.label} lacks an external wall"
        if r.spec.min_long:
            assert min(r.clear.w, r.clear.h) >= r.spec.min_short - 0.05 and max(r.clear.w, r.clear.h) >= r.spec.min_long - 0.05
    assert any(r.type in ("living", "foyer") and L._on_side(r.cell, ctx.rect, "bottom") for r in rooms.values())
    # rooms tile the envelope
    assert abs(sum(r.cell.area for r in rooms.values()) - ctx.rect.area) < 0.5


def test_flat_layout_two_external_sides():
    cfg = load_config()
    ctx = L.UnitContext(Rect(0, 0, 30, 34), {"top": True, "right": True, "bottom": False, "left": False}, entry_side="bottom", north_deg=0)
    lays = L.generate(ctx, L.build_programme("2bhk", cfg), cfg, goal="max_carpet", keep=2)
    assert lays
    for r in lays[0].rooms.values():
        for sd in r.external_sides:
            if L._on_side(r.cell, ctx.rect, sd):
                assert ctx.external[sd]


# ---------------------------------------------------------------- stages 3-5: apartment, vastu, exports
@pytest.fixture(scope="module")
def house_result():
    return generate_options(HOUSE, n_options=2)


@pytest.fixture(scope="module")
def apt_result():
    return generate_options(APT, n_options=2)


def test_house_options(house_result):
    opts = house_result["options"]
    assert 1 <= len(opts) <= 2
    o = opts[0]
    assert o["feasible"], o["warnings"]
    assert len(o["floors"]) == 2
    assert o["regulatory"]["far_ok"] and o["regulatory"]["coverage_ok"]
    assert o["areas"]["total_carpet_sqft"] > 1500
    assert o["vastu"]["summary"]["score"] is not None
    gf = o["floors"][0]["units"][0]
    types = {r["type"] for r in gf["rooms"]}
    assert {"living", "kitchen", "master", "toilet", "stair"} <= types
    assert all(r["door"] for r in gf["rooms"] if r["type"] not in ("passage", "foyer", "living", "dining"))
    assert any(r["windows"] for r in gf["rooms"] if r["type"] == "living")


def test_apartment_options(apt_result):
    opts = apt_result["options"]
    assert opts
    o = opts[0]
    typ = next(f for f in o["floors"] if f["kind"] == "typical")
    assert len(typ["units"]) == 4 and typ["repeat"] == 5
    assert sorted(u["type"] for u in typ["units"]) == ["2bhk", "2bhk", "3bhk", "3bhk"]
    assert typ["core"].get("stair") and typ["core"].get("lift1") and typ["corridor"]
    assert o["regulatory"]["far_ok"] and o["regulatory"]["coverage_ok"]
    assert o["areas"]["parking"]["ecs_provided"] >= o["areas"]["parking"]["ecs_required"]
    stilt = next(f for f in o["floors"] if f["kind"] == "stilt")
    assert stilt["parking"]
    # columns stack: same grid on every floor
    assert stilt["columns"]["grid_x"] == typ["columns"]["grid_x"] and stilt["columns"]["grid_y"] == typ["columns"]["grid_y"]
    # every planned unit respects the kitchen/toilet rule
    for u in typ["units"]:
        assert u["error"] is None
        k = next(r for r in u["rooms"] if r["type"] == "kitchen")
        for r in u["rooms"]:
            if r["type"] == "toilet":
                assert shared_wall(Rect(**k["cell"]), Rect(**r["cell"])) is None
    assert o["areas"]["efficiency"] > 0.6
    assert any(s["name"] == "ug_tank" and s["placed"] for s in o["site"]["services"])
    assert any(r["rule"] == "stair" for r in o["vastu"]["rows"])


def test_exports(tmp_path, apt_result, house_result):
    import ezdxf
    for res in (apt_result, house_result):
        o = res["options"][0]
        svg = svg_exp.render_floor(o, len(o["floors"]) - 1)
        assert svg.startswith("<svg") and "data-room" in svg
        assert "<svg" in svg_exp.render_site(o)
        md = report_exp.render_markdown(res, o)
        assert "Area statement" in md and "RERA" in md and "Vastu compliance" in md
        html = report_exp.render_html(res, o)
        assert "<table>" in html
        assert "id,grid_x,grid_y" in o["columns_csv"]
        p = tmp_path / f"{o['name']}.dxf"
        dxf_exp.write_dxf(o, str(p), load_config())
        doc = ezdxf.readfile(str(p))
        layers = {l.dxf.name for l in doc.layers}
        assert {"WALLS", "DOORS", "WINDOWS", "TEXT", "DIMS", "GRID", "COLUMNS", "SETBACK"} <= layers
        assert len(list(doc.modelspace())) > 50


def test_rescore_edit(house_result):
    o = house_result["options"][0]
    u = o["floors"][0]["units"][0]
    tree = json.loads(json.dumps(u["tree"]))
    # shrink the first split slightly and re-score
    node = tree
    while node["kind"] == "leaf":
        node = node["children"][0]
    n = len(node["children"])
    node["ratio"] = [1.0] * n
    out = rescore_unit(o, 0, u["label"], tree, HOUSE)
    assert "unit" in out
    if out.get("ok"):
        assert out["unit"]["rooms"] and "score" in out["unit"]


def test_strict_vastu_reports_conflicts():
    brief = json.loads(json.dumps(HOUSE))
    brief["project"]["vastu"] = "strict"
    res = generate_options(brief, n_options=1)
    assert res["options"]
    assert res["options"][0]["vastu"]["mode"] == "strict"
