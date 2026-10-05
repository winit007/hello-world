"""Starts the Stock Agent server inside the Android app (called from Kotlin through Chaquopy)."""
import os
import sys


def start(app_dir, data_dir, key):
    """Serve the app on 127.0.0.1 until the process ends. `key` is private to the app: requests without it get a 403."""
    os.environ["STOCK_AGENT_HOME"] = data_dir
    os.environ["STOCK_AGENT_ANDROID"] = "1"
    os.makedirs(data_dir, exist_ok=True)
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    from stock_agent import app

    app.PHONE.update(on=True, key=key, lock_local=True)       # other apps on the phone must not read your trades
    app.serve(8765, open_browser=False)


def port():
    """The port the server is listening on (0 until it has started)."""
    from stock_agent import app

    return app.LISTEN_PORT
