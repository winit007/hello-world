"""Minimal HTTP server (stdlib) exposing the planner to the web UI.

Routes:
  GET  /                      -> web/index.html
  GET  /static/<file>         -> web assets
  GET  /api/defaults          -> config defaults for the wizard
  POST /api/generate          -> {brief} => {session, result}
  POST /api/rescore           -> {session, option, floor, unit, tree} => updated unit
  GET  /api/export/<session>/<option>/<fmt>[?floor=i]  fmt: json | svg | site.svg | dxf | csv | md | html
"""
from __future__ import annotations
import json
import os
import tempfile
import threading
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from .config import load_defaults, load_config
from .engine import generate_options, rescore_unit
from .exporters import svg as svg_exp, dxf as dxf_exp, report as report_exp

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
SESSIONS: dict[str, dict] = {}
LOCK = threading.Lock()
MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".json": "application/json"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter
        pass

    def _send(self, status: int, body: bytes, ctype: str = "application/json", download: str | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path == "/" or path == "/index.html":
            return self._static("index.html")
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/api/defaults":
            cfg = load_defaults()
            return self._json(200, {"config": cfg, "unit_types": list(cfg["rooms"]["unit_programmes"].keys()), "goals": list(cfg["scoring"]["goals"].keys())})
        if path.startswith("/api/export/"):
            parts = path.split("/")[3:]
            if len(parts) < 3:
                return self._json(404, {"error": "bad export path"})
            sid, oid, fmt = parts[0], parts[1], "/".join(parts[2:])
            q = parse_qs(u.query)
            return self._export(sid, oid, fmt, int(q.get("floor", ["0"])[0]))
        return self._json(404, {"error": "not found"})

    def _static(self, name: str):
        fp = os.path.normpath(os.path.join(WEB_DIR, name))
        if not fp.startswith(WEB_DIR) or not os.path.isfile(fp):
            return self._send(404, b"not found", "text/plain")
        with open(fp, "rb") as f:
            data = f.read()
        self._send(200, data, MIME.get(os.path.splitext(fp)[1], "application/octet-stream"))

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid JSON"})
        try:
            if u.path == "/api/generate":
                brief = body.get("brief") or body
                result = generate_options(brief, n_options=int(body.get("n_options", 4)))
                sid = uuid.uuid4().hex[:12]
                with LOCK:
                    SESSIONS[sid] = {"brief": brief, "result": result}
                return self._json(200, {"session": sid, "result": result})
            if u.path == "/api/rescore":
                sid = body["session"]
                with LOCK:
                    sess = SESSIONS.get(sid)
                if not sess:
                    return self._json(404, {"error": "session expired; generate again"})
                opt = next(o for o in sess["result"]["options"] if o["id"] == int(body["option"]))
                out = rescore_unit(opt, int(body["floor"]), body["unit"], body["tree"], sess["brief"])
                if out.get("ok"):
                    f = opt["floors"][int(body["floor"])]
                    for i, un in enumerate(f["units"]):
                        if un["label"] == body["unit"]:
                            f["units"][i] = out["unit"]
                    _refresh_option_totals(opt)
                return self._json(200, out)
        except Exception as e:  # surface engine errors to the UI
            import traceback
            return self._json(500, {"error": str(e), "trace": traceback.format_exc()})
        return self._json(404, {"error": "not found"})

    def _export(self, sid: str, oid: str, fmt: str, floor: int):
        with LOCK:
            sess = SESSIONS.get(sid)
        if not sess:
            return self._json(404, {"error": "session expired"})
        result = sess["result"]
        opt = next((o for o in result["options"] if o["id"] == int(oid)), None)
        if opt is None:
            return self._json(404, {"error": "option not found"})
        cfg = load_config(overrides=sess["brief"].get("config"))
        name = f"option{oid}"
        if fmt == "json":
            return self._send(200, json.dumps(opt, indent=1).encode(), "application/json", f"{name}.json")
        if fmt == "svg":
            return self._send(200, svg_exp.render_floor(opt, floor, show_zones=True).encode(), "image/svg+xml", f"{name}_floor{floor}.svg")
        if fmt == "site.svg":
            return self._send(200, svg_exp.render_site(opt).encode(), "image/svg+xml", f"{name}_site.svg")
        if fmt == "csv":
            return self._send(200, opt["columns_csv"].encode(), "text/csv", f"{name}_columns.csv")
        if fmt == "md":
            return self._send(200, report_exp.render_markdown(result, opt).encode(), "text/markdown", f"{name}_report.md")
        if fmt == "html":
            return self._send(200, report_exp.render_html(result, opt).encode(), "text/html; charset=utf-8")
        if fmt == "dxf":
            with tempfile.TemporaryDirectory() as td:
                p = os.path.join(td, f"{name}.dxf")
                dxf_exp.write_dxf(opt, p, cfg)
                with open(p, "rb") as f:
                    data = f.read()
            return self._send(200, data, "application/dxf", f"{name}.dxf")
        return self._json(404, {"error": "unknown format"})


def _refresh_option_totals(opt: dict) -> None:
    """Recompute per-floor and option area totals after a unit edit."""
    factor = opt["areas"]["super_builtup_factor"]
    total_carpet = 0.0
    total_ub = 0.0
    for f in opt["floors"]:
        ub = sum(u["builtup_sqft"] for u in f["units"])
        carpet = sum(u["carpet_sqft"] for u in f["units"])
        f["areas"].update({"unit_builtup_sqft": round(ub, 1), "carpet_sqft": round(carpet, 1), "common_sqft": round(f["areas"]["builtup_sqft"] - ub, 1),
                           "efficiency": round(carpet / (ub * factor), 3) if ub else 0.0})
        total_carpet += carpet * f["repeat"]
        total_ub += ub * f["repeat"]
    opt["areas"]["total_carpet_sqft"] = round(total_carpet, 1)
    opt["areas"]["efficiency"] = round(total_carpet / (total_ub * factor), 3) if total_ub else 0.0
    for us in opt["areas"]["units"]:
        for f in opt["floors"]:
            if f["name"] == us["floor"]:
                for u in f["units"]:
                    if u["label"] == us["label"]:
                        us.update({"carpet_sqft": u["carpet_sqft"], "balcony_sqft": u["balcony_sqft"], "score": u["score"], "vastu": u.get("scores", {}).get("vastu")})


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Space Planner UI at http://{host}:{port}/  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
