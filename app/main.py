from __future__ import annotations

import logging
import sys
import time
import urllib.request
from threading import Thread

import uvicorn
import webview
from app.api import app as fastapi_app

API_HOST = "127.0.0.1"
API_PORT = 8000
APP_URL = f"http://{API_HOST}:{API_PORT}"
APP_NAME = "GitFinder"
STARTUP_TIMEOUT_SECONDS = 15


def start_api_server() -> None:
    """Run FastAPI in a background thread. It also serves the built frontend
    (frontend/dist) at "/", so this single server is the whole app backend."""
    uvicorn.run(
        fastapi_app,
        host=API_HOST,
        port=API_PORT,
        log_level="info",
    )


def _server_is_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{APP_URL}/api/health", timeout=0.5) as response:
            return response.status == 200
    except OSError:
        return False


def _wait_for_server() -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _server_is_ready():
            return
        time.sleep(0.05)
    logging.getLogger(__name__).warning(
        "API server did not respond within %s seconds; loading window anyway.",
        STARTUP_TIMEOUT_SECONDS,
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    api_thread = Thread(target=start_api_server, daemon=True)
    api_thread.start()
    _wait_for_server()

    # pywebview renders through the OS's own webview (WKWebView on macOS,
    # WebView2 on Windows, WebKitGTK on Linux) instead of bundling Chromium,
    # which keeps a packaged build small.
    webview.create_window(APP_NAME, APP_URL, width=1400, height=900, min_size=(900, 600))
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())