from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import uvicorn
from PySide6.QtCore import QUrl, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtWebEngineWidgets import QWebEngineView


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
API_HOST = "127.0.0.1"
API_PORT = 8000
FRONTEND_URL = "http://127.0.0.1:5173"


def start_api_server() -> None:
    """Run FastAPI in a background thread so the Qt UI can use it."""
    uvicorn.run(
        "app.api:app",
        host=API_HOST,
        port=API_PORT,
        log_level="info",
    )


def start_frontend_dev_server() -> subprocess.Popen:
    """Start the Vite React development server."""
    npm = "npm"
    if sys.platform == "win32":
        npm = "npm.cmd"

    return subprocess.Popen(
        [npm, "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"],
        cwd=str(FRONTEND_DIR),
        stdout=None,
        stderr=None,
        env=os.environ.copy(),
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    application = QApplication(sys.argv)
    application.setApplicationName("Visual Git Workspace")

    # Start FastAPI in a background thread.
    from threading import Thread

    api_thread = Thread(target=start_api_server, daemon=True)
    api_thread.start()

    # Start the React/Vite frontend.
    frontend_process = start_frontend_dev_server()

    # Give the two local servers a moment to start before loading the UI.
    window = QMainWindow()
    window.setWindowTitle("Visual Git Workspace")
    window.resize(1400, 900)

    webview = QWebEngineView()
    window.setCentralWidget(webview)
    window.show()

    def load_frontend() -> None:
        webview.setUrl(QUrl(FRONTEND_URL))

    QTimer.singleShot(2500, load_frontend)

    def cleanup() -> None:
        if frontend_process.poll() is None:
            frontend_process.terminate()
            try:
                frontend_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                frontend_process.kill()

    application.aboutToQuit.connect(cleanup)

    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
