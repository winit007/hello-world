#!/data/data/com.termux/files/usr/bin/bash
# Installs the full Stock Agent app on an Android phone, inside Termux.
#
#   curl -fsSL https://winit007.github.io/hello-world/termux.sh | bash
#
# Run it again any time to update. Afterwards type `stock-agent` (or tap the Termux:Widget icon): the app
# starts on this phone and opens in Chrome at http://127.0.0.1:8765, with live scans, stock lookup,
# paper trading and Kite orders. It runs only on the phone; nothing is uploaded anywhere.
set -euo pipefail

ZIP="${STOCK_AGENT_ZIP:-https://github.com/winit007/hello-world/archive/refs/heads/master.zip}"
say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

if [ -z "${PREFIX:-}" ] || [ ! -d "$PREFIX" ] || ! command -v pkg >/dev/null 2>&1; then
  echo "This installer is for Termux on Android. On a PC, use: python -m pip install $ZIP"; exit 1
fi

# A running copy of the app would keep the old code loaded: stop it first (run this from any Termux window)
if pkill -f "stock_agent app" 2>/dev/null; then say "Stopped the running Stock Agent; start it again with: stock-agent"; sleep 1; fi

# Package steps read from /dev/null: with `curl | bash` a prompt would otherwise swallow the rest of this script
APT=(-y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)
say "Updating Termux packages"
pkg update "${APT[@]}" </dev/null || true
pkg install "${APT[@]}" python termux-tools </dev/null

say "Installing numpy and pandas (ready-made Termux packages)"
pkg install "${APT[@]}" tur-repo </dev/null
pkg update "${APT[@]}" </dev/null || true
pkg install "${APT[@]}" python-numpy python-pandas </dev/null
python -c "import numpy, pandas" || {
  echo "numpy/pandas did not install. Run: pkg install tur-repo && pkg install python-numpy python-pandas"; exit 1; }

pipi() { python -m pip install --disable-pip-version-check "$@" </dev/null ||
         python -m pip install --disable-pip-version-check --break-system-packages "$@" </dev/null; }

say "Installing the small Python libraries"
pipi requests feedparser vaderSentiment qrcode tzdata   # tzdata: Android has no time-zone database

say "Installing Stock Agent"
# --no-deps: yfinance needs curl_cffi, which does not build on Android; the app has its own Yahoo client
pipi --no-deps --force-reinstall --no-cache-dir "$ZIP"

say "Creating the launcher"
cat > "$PREFIX/bin/stock-agent" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
# Start Stock Agent on this phone. Keep Termux open in the background; Ctrl+C or close the session to stop.
URL="http://127.0.0.1:8765/"
if curl -s -o /dev/null --max-time 3 "$URL"; then   # already running (e.g. a second tap): just open it
  echo "Stock Agent is already running. Opening $URL"
  termux-open-url "$URL" 2>/dev/null || echo "Open $URL in Chrome."
  sleep 2; exit 0
fi
echo "Starting Stock Agent… Chrome opens in a few seconds (or open $URL yourself)."
termux-wake-lock 2>/dev/null || true          # keep scans running while the screen is off
python -m stock_agent app --port 8765 "$@"
code=$?
termux-wake-unlock 2>/dev/null || true
if [ "$code" -ne 0 ] && [ "$code" -ne 130 ] && [ "$code" -ne 143 ]; then   # 130 Ctrl+C, 143 stopped by the installer
  echo; echo "Stock Agent stopped with an error (code $code). Take a screenshot of this screen."
  read -r -p "Press Enter to close. " _ || true
fi
exit "$code"
EOF
chmod 755 "$PREFIX/bin/stock-agent"

# Home-screen icon through the Termux:Widget add-on, if the person installs it
mkdir -p "$HOME/.shortcuts"
chmod 700 "$HOME/.shortcuts"                  # Termux:Widget ignores a folder others can write to
printf '#!/data/data/com.termux/files/usr/bin/bash\n"%s/bin/stock-agent"\n' "$PREFIX" > "$HOME/.shortcuts/Stock Agent"
chmod 700 "$HOME/.shortcuts/Stock Agent"
# refresh the widget list if Termux:Widget is already installed
am broadcast -n com.termux.widget/.TermuxWidgetProvider -a com.termux.widget.ACTION_REFRESH_WIDGET >/dev/null 2>&1 || true

python -c "import stock_agent; print('Stock Agent', stock_agent.__version__, 'installed')"
cat <<'MSG'

Done. To open the app, type:   stock-agent
It opens in Chrome at http://127.0.0.1:8765 (keep Termux running in the background).

Tips
  * Home-screen icon: install "Termux:Widget" (same place you got Termux), add its widget,
    and tap "Stock Agent".
  * Android may stop Termux to save battery: Settings > Apps > Termux > Battery > Unrestricted.
  * The first scan downloads 10 years of prices (a few minutes on mobile data, about 50 MB).
  * Zerodha Kite: in your Kite Connect app set the redirect URL to http://127.0.0.1:8765/kite/callback
    (the same one the PC app uses), then enter your API key in Settings.
  * Update later: run the same curl command again.
MSG
