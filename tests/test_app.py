import json
import math
import os
import tempfile
import threading
import unittest
import urllib.request
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["STOCK_AGENT_HOME"] = cls.tmp.name
        import importlib

        from stock_agent import app
        cls.app = importlib.reload(app)
        from http.server import ThreadingHTTPServer
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), cls.app.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()
        os.environ.pop("STOCK_AGENT_HOME", None)

    def call(self, path, body=None, method=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method or ("POST" if data else "GET"),
                                     headers={"Content-Type": "application/json"})
        req.add_header("X-Agent-Token", self.app.SESSION_TOKEN)
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return json.loads(raw) if r.headers.get("Content-Type", "").startswith("application/json") else raw

    def test_clean(self):
        out = self.app.clean({"a": float("nan"), "b": np.int64(3), "c": pd.Timestamp("2026-10-01"), "d": [np.float64(1.5), math.inf]})
        self.assertEqual(out, {"a": None, "b": 3, "c": "2026-10-01", "d": [1.5, None]})
        json.dumps(out, allow_nan=False)

    def test_index_and_settings(self):
        html = self.call("/")
        self.assertIn(b"Stock Agent", html)
        s = self.call("/api/settings", {"capital": 200000, "risk": 50, "horizon": 7, "bogus": 1})
        self.assertEqual(s["capital"], 200000)
        self.assertEqual(s["risk"], 20.0)      # clamped
        self.assertEqual(s["horizon"], 5)      # only 3/5/10 allowed
        self.assertNotIn("bogus", s)
        self.assertEqual(self.call("/api/settings")["capital"], 200000)

    def test_journal_round_trip(self):
        j = self.call("/api/journal", {"ticker": "TCS.NS", "contract": "TCS 27-Oct-2026 2080 PE", "side": "PUT",
                                       "lots": 2, "lot_size": 225, "entry_premium": 70, "stop": 2120, "target1": 2006,
                                       "target2": 1961, "time_exit": "2026-10-08", "pattern": "x"})
        tid = j["entries"][0]["id"]
        self.assertEqual(j["summary"]["open"], 1)
        j = self.call(f"/api/journal/{tid}", {"exit_premium": 60})
        e = j["entries"][0]
        self.assertEqual(e["status"], "closed")
        self.assertAlmostEqual(e["pnl"], (60 - 70) * 225 * 2)
        self.assertEqual(j["summary"]["wins"], 0)
        j = self.call(f"/api/journal/{tid}", method="DELETE")
        self.assertEqual(j["entries"], [])

    def test_shortcut(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / "Desktop").mkdir()
            old = os.environ.get("HOME"), os.environ.get("USERPROFILE")
            os.environ["HOME"] = os.environ["USERPROFILE"] = home
            try:
                path = self.app.make_shortcut()
            finally:
                for k, v in zip(("HOME", "USERPROFILE"), old):
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            self.assertEqual(path.parent, Path(home) / "Desktop")
            self.assertIn("-m stock_agent app", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class PhoneModeTests(unittest.TestCase):
    """The server reached from another device: key required once, then a cookie; plain-IP Host only."""

    @classmethod
    def setUpClass(cls):
        import importlib
        import socket
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["STOCK_AGENT_HOME"] = cls.tmp.name
        from stock_agent import app
        cls.app = importlib.reload(app)
        ips = cls.app._lan_ips()
        if not ips:
            raise unittest.SkipTest("no non-loopback address in this environment")
        cls.ip = ips[0]
        cls.app.PHONE.update(on=True, key="testkey123")
        from http.server import ThreadingHTTPServer
        cls.httpd = ThreadingHTTPServer(("0.0.0.0", 0), cls.app.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.app.PHONE.update(on=False, key=None)
        cls.tmp.cleanup()
        os.environ.pop("STOCK_AGENT_HOME", None)

    def get(self, path, headers=None, ip=None):
        import http.client
        c = http.client.HTTPConnection(ip or self.ip, self.port, timeout=10)
        c.request("GET", path, headers=headers or {})
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def test_key_cookie_flow(self):
        status, _, body = self.get("/")
        self.assertEqual(status, 403)
        self.assertIn(b"?key=", body)
        self.assertEqual(self.get("/?key=wrong")[0], 403)
        status, headers, _ = self.get("/?key=testkey123")
        self.assertEqual(status, 303)
        cookie = headers["Set-Cookie"].split(";")[0]
        status, _, page = self.get("/", {"Cookie": cookie})
        self.assertEqual(status, 200)
        token = self.app.SESSION_TOKEN
        self.assertIn(token.encode(), page)
        self.assertEqual(self.get("/api/settings", {"Cookie": cookie, "X-Agent-Token": token})[0], 200)
        self.assertEqual(self.get("/api/settings", {"X-Agent-Token": token})[0], 403)          # no cookie
        self.assertEqual(self.get("/api/settings", {"Cookie": cookie, "X-Agent-Token": token,
                                                    "Host": "evil.example"})[0], 403)        # rebinding

    def test_icons_and_manifest_without_key(self):
        status, headers, body = self.get("/manifest.webmanifest")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["display"], "standalone")
        status, headers, body = self.get("/icon-192.png")
        self.assertEqual((status, body[:4]), (200, b"\x89PNG"))

    def test_this_computer_needs_no_key(self):
        self.assertEqual(self.get("/", ip="127.0.0.1")[0], 200)
