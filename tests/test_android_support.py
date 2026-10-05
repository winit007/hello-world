"""What the Android app relies on: the self-test, the server lock for other apps, notifications."""
import http.cookiejar
import re
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from stock_agent import alerts as AL
from stock_agent import app, selftest


class SelfTestTests(unittest.TestCase):
    def test_every_local_check_passes(self):
        r = selftest.run(network=False)
        self.assertTrue(r["ok"], [c for c in r["checks"] if not c["ok"]])
        self.assertGreaterEqual(len(r["checks"]), 7)

    def test_a_failing_check_is_reported_not_raised(self):
        out = selftest._check("boom", lambda: 1 / 0)
        self.assertFalse(out["ok"])
        self.assertIn("ZeroDivisionError", out["detail"])


class LockTests(unittest.TestCase):
    def serve(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.addCleanup(httpd.server_close)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(self, url, opener=None):
        try:
            return (opener or urllib.request.build_opener()).open(url, timeout=10)
        except urllib.error.HTTPError as e:
            return e

    def test_other_apps_on_the_phone_need_the_key_but_the_app_gets_in(self):
        base = self.serve()
        key = "k" * 24
        with mock.patch.dict(app.PHONE, {"on": True, "key": key, "lock_local": True}):
            self.assertEqual(self.get(base + "/").code, 403)                 # another app on the phone: no key
            self.assertEqual(self.get(base + "/api/settings").code, 403)
            self.assertEqual(self.get(base + "/?key=wrong" + "x" * 10).code, 403)
            jar = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            page = self.get(base + f"/?key={key}", opener)                   # the app's own window: key once, then a cookie
            self.assertEqual(page.status, 200)
            html = page.read()
            self.assertIn(b"Stock Agent", html)
            token = re.search(rb'name="agent-token" content="([^"]+)"', html).group(1).decode()
            req = urllib.request.Request(base + "/api/settings", headers={"X-Agent-Token": token})
            self.assertEqual(opener.open(req, timeout=10).status, 200)       # the cookie plus the page's token, as in the real app
            no_cookie = urllib.request.Request(base + "/api/settings", headers={"X-Agent-Token": token})
            self.assertEqual(self.get(no_cookie).code, 403)                    # the token alone (another app) is not enough

    def test_normal_pc_use_is_not_locked(self):
        base = self.serve()
        with mock.patch.dict(app.PHONE, {"on": False, "key": None, "lock_local": False}):
            self.assertEqual(self.get(base + "/").status, 200)


class NotificationTests(unittest.TestCase):
    def test_alerts_go_to_android_notifications_inside_the_app(self):
        sent = []
        with mock.patch.dict("os.environ", {"STOCK_AGENT_ANDROID": "1"}), \
                mock.patch.object(AL, "android_notify", side_effect=lambda t, x: sent.append((t, x))):
            problems = AL.deliver([{"title": "Stop-loss hit: TCS", "text": "at 3,890"}], {})
        self.assertEqual(problems, [])
        self.assertEqual(sent, [("Stop-loss hit: TCS", "at 3,890")])

    def test_a_failing_notifier_is_reported(self):
        with mock.patch.dict("os.environ", {"STOCK_AGENT_ANDROID": "1"}), \
                mock.patch.object(AL, "android_notify", side_effect=RuntimeError("no channel")):
            problems = AL.deliver([{"title": "t", "text": "x"}], {})
        self.assertIn("no channel", problems[0])


if __name__ == "__main__":
    unittest.main()
