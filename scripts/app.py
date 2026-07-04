"""
scripts/app.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Application entry point — lives inside scripts/ alongside all other code.

Starts the FastAPI backend in a background thread, then opens the React UI
in a native desktop window via pywebview.

White-screen fix
────────────────
pywebview's native window has no auto-retry if the URL isn't responding
yet at the moment create_window() is called — it just shows a blank page
forever.  A fixed sleep() before opening the window is a guess that can
lose the race, especially right after Vite re-optimizes dependencies
(which can take several seconds longer than a cold start).

wait_for_url() actively polls the target URL with real HTTP requests
until it gets a response, then opens the window only once the page is
guaranteed to load successfully on the first try.

The PyWebViewApi class exposes Python functions to the JavaScript frontend
via  window.pywebview.api.<method>()  — used for native file/folder pickers
in the Library page's Import Episode modal.

Usage
─────
    Development (Vite hot-reload active):
        Terminal 1:  cd ui && npm run dev
        Terminal 2:  python scripts/app.py  (from project root)
                  OR cd scripts && python app.py

    Production (built React bundle):
        cd ui && npm run build
        Set DEV_MODE = False below
        python scripts/app.py

Ports
─────
    FastAPI  →  http://127.0.0.1:8000
    Vite     →  http://127.0.0.1:5173  (dev only)
"""

from __future__ import annotations

import sys
import time
import threading
import urllib.request
import urllib.error
import logging
from pathlib import Path

from logging_setup import setup_logging
setup_logging()

SCRIPTS_DIR  = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent

API_HOST = "127.0.0.1"
API_PORT = 8000

# Vite can bind to either 127.0.0.1 or localhost depending on system DNS
# resolution (localhost sometimes resolves to ::1 / IPv6 on macOS).
# Try both so the polling isn't fooled by a hostname mismatch.
VITE_URLS = ["http://127.0.0.1:5173", "http://localhost:5173"]

# DEV_MODE = True  → pywebview opens the Vite dev server (hot reload)
# DEV_MODE = False → pywebview opens FastAPI serving the built React bundle
DEV_MODE = True


# ── Wait-for-server helper ─────────────────────────────────────────────────────

def wait_for_url(url: str, timeout: float = 30.0, interval: float = 0.3) -> bool:
    """
    Poll a URL with real HTTP requests until it responds or timeout elapses.

    Used instead of a fixed sleep() before opening the pywebview window —
    a guessed delay can lose the race against slow Vite startup (e.g. after
    dependency re-optimization), leaving the window permanently blank with
    no retry.  Polling guarantees the window only opens once the page is
    actually ready to load.

    Returns True if the URL responded (any HTTP status counts — even 404
    means the server is up), False if timeout was reached first.
    """
    deadline   = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2.0)
            return True
        except Exception as exc:
            last_error = exc
            time.sleep(interval)
    if last_error:
        print(f"   (last error polling {url}: {last_error})")
    return False


def wait_for_any_url(urls: list, timeout: float = 30.0, interval: float = 0.3) -> str | None:
    """
    Poll multiple candidate URLs round-robin until one responds.
    Returns the URL that responded, or None if all timed out.
    """
    deadline = time.time() + timeout
    last_errors = {}
    while time.time() < deadline:
        for url in urls:
            try:
                urllib.request.urlopen(url, timeout=2.0)
                return url
            except Exception as exc:
                last_errors[url] = exc
        time.sleep(interval)
    for url, exc in last_errors.items():
        print(f"   (last error polling {url}: {exc})")
    return None


# ── PyWebView API ─────────────────────────────────────────────────────────────
# Methods here are callable from JS as  window.pywebview.api.method_name()
# They run on the Python side and their return values are Promise-resolved in JS.

class PyWebViewApi:
    """
    Python functions exposed to the JavaScript frontend.
    The window reference is set after create_window() returns.
    """

    def __init__(self):
        self._window = None

    def pick_file(self, file_types: list = None) -> str | None:
        """
        Show the native OS file-open dialog.

        file_types — list of strings like "Video Files (*.mp4;*.mkv)",
                     or None for "All Files (*.*)".

        Returns the selected absolute path, or None if cancelled.
        """
        import webview
        if not self._window:
            return None
        types = tuple(file_types) if file_types else ("All Files (*.*)",)
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple = False,
            file_types     = types,
        )
        return result[0] if result else None

    def pick_folder(self) -> str | None:
        """
        Show the native OS folder-select dialog.
        Returns the selected absolute path, or None if cancelled.
        """
        import webview
        if not self._window:
            return None
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        return result[0] if result else None

    def save_file(self, src_path: str, suggested_name: str = None) -> str | None:
        """
        Show the native OS save dialog and COPY src_path to the chosen location.

        Used by the Dubbing studio "Save" buttons — a real download (a webview
        <a download> just navigates the window to the file). Returns the saved
        path, or None if cancelled / source missing.
        """
        import shutil
        from pathlib import Path as _P
        import webview
        if not self._window or not src_path or not _P(src_path).exists():
            return None
        name   = suggested_name or _P(src_path).name
        result = self._window.create_file_dialog(webview.SAVE_DIALOG, save_filename=name)
        if not result:
            return None
        dest = result if isinstance(result, str) else result[0]
        try:
            shutil.copy(src_path, dest)
            return dest
        except Exception:
            return None


# ── Backend thread ─────────────────────────────────────────────────────────────

def _start_api():
    import uvicorn
    from api.main import app
    uvicorn.run(
        app,
        host      = API_HOST,
        port      = API_PORT,
        log_level = "warning",
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    api_thread = threading.Thread(target=_start_api, daemon=True, name="api")
    api_thread.start()

    api_url = f"http://{API_HOST}:{API_PORT}/api/health"
    print(f"Waiting for API at {api_url} …")
    if not wait_for_url(api_url, timeout=20):
        print("⚠  API did not respond within 20s — opening window anyway, "
              "but data calls may fail until it's up.")
    else:
        print("API is up ✓")

    target_url = None

    if DEV_MODE:
        print(f"Waiting for Vite dev server at {VITE_URLS} …")
        target_url = wait_for_any_url(VITE_URLS, timeout=30)
        if not target_url:
            print(
                "⚠  Vite did not respond within 30s on either 127.0.0.1 or localhost.\n"
                "   Make sure 'npm run dev' is running in the ui/ folder, "
                "then restart this script.\n"
                "   If it's running, try opening http://localhost:5173 in a normal "
                "browser to confirm it actually responds there."
            )
            sys.exit(1)
        print(f"Vite is up ✓  ({target_url})")
    else:
        target_url = f"http://{API_HOST}:{API_PORT}"

    import webview

    pwa = PyWebViewApi()

    window = webview.create_window(
        title    = "ManhwaStudio",
        url      = target_url,
        width    = 1280,
        height   = 800,
        min_size = (960, 660),
        js_api   = pwa,
        text_select = True,   # allow selecting/copying text (e.g. console errors)
    )

    # Give the API object a reference to the window so file dialogs work
    pwa._window = window

    webview.start(private_mode=False)