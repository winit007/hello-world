"""CLI: python -m spaceplanner generate brief.json -o out/ | python -m spaceplanner serve [--port 8000]"""
from __future__ import annotations
import argparse
import json
import os
import sys

from .config import load_config
from .engine import generate_options
from .exporters import svg as svg_exp, dxf as dxf_exp, report as report_exp


def cmd_generate(args) -> int:
    with open(args.brief, "r", encoding="utf-8") as f:
        brief = json.load(f)
    result = generate_options(brief, n_options=args.options)
    os.makedirs(args.out, exist_ok=True)
    cfg = load_config(overrides=brief.get("config"))
    with open(os.path.join(args.out, "result.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in result.items() if k != "brief"}, f, indent=1)
    for opt in result["options"]:
        d = os.path.join(args.out, f"option_{opt['id']}")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "option.json"), "w", encoding="utf-8") as f:
            json.dump(opt, f, indent=1)
        for i, fl in enumerate(opt["floors"]):
            with open(os.path.join(d, f"floor_{i}_{fl['kind']}.svg"), "w", encoding="utf-8") as f:
                f.write(svg_exp.render_floor(opt, i, show_zones=True))
        with open(os.path.join(d, "site.svg"), "w", encoding="utf-8") as f:
            f.write(svg_exp.render_site(opt))
        dxf_exp.write_dxf(opt, os.path.join(d, "plan.dxf"), cfg)
        with open(os.path.join(d, "report.md"), "w", encoding="utf-8") as f:
            f.write(report_exp.render_markdown(result, opt))
        with open(os.path.join(d, "report.html"), "w", encoding="utf-8") as f:
            f.write(report_exp.render_html(result, opt))
        with open(os.path.join(d, "columns.csv"), "w", encoding="utf-8") as f:
            f.write(opt["columns_csv"])
        print(f"{opt['name']}: score {opt['score']} - {opt['summary']}  -> {d}")
    for w in result["warnings"]:
        print("  !", w)
    return 0


def cmd_serve(args) -> int:
    from .server import serve
    serve(args.host, args.port)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="spaceplanner", description="Architectural space planner for Indian residential / commercial buildings")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="generate layout options from a brief JSON")
    g.add_argument("brief")
    g.add_argument("-o", "--out", default="out")
    g.add_argument("-n", "--options", type=int, default=4)
    g.set_defaults(func=cmd_generate)
    s = sub.add_parser("serve", help="run the web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
