#!/usr/bin/env bash
# Runs inside an Android emulator on GitHub's servers: installs the debug build, opens the app and checks, from the
# outside, that the Python server inside it started, is locked to the app, serves the page and passes its self-test.
set -u
APK="$1"
KEY="debug-key-for-the-build-test-0123456789abcdef"      # the debug build uses a fixed key (see AgentRuntime.kt)
OUT="${2:-smoke_out}"
mkdir -p "$OUT"

logs() {
  echo "=========== logcat (python, app, crashes) ==========="
  adb logcat -d -s python.stdout:* python.stderr:* StockAgent:* AndroidRuntime:* Chaquopy:* 2>&1 | tail -${1:-200}
}
fail() { echo "FAILED: $1"; logs 300; adb exec-out screencap -p > "$OUT/screen_failed.png" 2>/dev/null || true; exit 1; }

adb install -r "$APK" || fail "could not install the APK"
adb shell pm grant com.stockagent.app android.permission.POST_NOTIFICATIONS || true
adb shell am start -n com.stockagent.app/.MainActivity || fail "could not start the app"
adb forward tcp:8765 tcp:8765

echo "waiting for the server inside the app (the first start unpacks Python, numpy and pandas)..."
code=000
for i in $(seq 1 150); do
  code=$(curl -s -o /dev/null -m 5 -w "%{http_code}" "http://127.0.0.1:8765/" || true)
  [ "$code" = "403" ] && break
  sleep 4
done
echo "page without the key -> HTTP $code (want 403: other apps must be locked out)"
[ "$code" = "403" ] || fail "the server did not come up locked"

jar="$OUT/cookies.txt"; : > "$jar"
curl -s -m 30 -c "$jar" -b "$jar" -L "http://127.0.0.1:8765/?key=$KEY" -o "$OUT/page.html" -w "page with the key -> HTTP %{http_code}\n"
grep -q "Stock Agent" "$OUT/page.html" || fail "the page did not load with the key"
TOKEN=$(grep -o 'name="agent-token" content="[^"]*"' "$OUT/page.html" | sed 's/.*content="//; s/"$//')
[ -n "$TOKEN" ] || fail "no app token in the page"

echo "running the self-test inside the app..."
curl -s -m 180 -b "$jar" -H "X-Agent-Token: $TOKEN" http://127.0.0.1:8765/api/selftest -o "$OUT/selftest.json" || fail "self-test request failed"
python3 - "$OUT/selftest.json" <<'PY' || fail "the self-test found problems"
import json, sys
r = json.load(open(sys.argv[1]))
print("python", r["python"], "|", r["platform"])
bad = 0
for c in r["checks"]:
    network = "HTTPS" in c["name"]
    mark = "ok  " if c["ok"] else ("warn" if network else "FAIL")
    print(f"  {mark} {c['name']:<28} {c['seconds']:>5}s  {c['detail']}")
    if not c["ok"] and not network:
        bad += 1
sys.exit(1 if bad else 0)
PY

adb shell dumpsys activity services com.stockagent.app | grep -q AgentService && echo "background service is running" || fail "the background service is not running"
sleep 8
adb exec-out screencap -p > "$OUT/screen.png" || true
echo "SMOKE TEST PASSED"
logs 40
