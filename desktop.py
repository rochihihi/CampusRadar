"""CampusRadar desktop entry point and scheduled notice scans."""
from pathlib import Path
import socket
import sys
import threading
import time
import webbrowser
from urllib.parse import urlsplit

import uvicorn
import webview
webview.settings['ALLOW_DOWNLOADS'] = True
from fastapi.staticfiles import StaticFiles


BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
sys.path.insert(0, str(BASE / "backend"))
from main import app  # noqa: E402
from agent import scan_all  # noqa: E402

STATIC = BASE / "frontend" / "dist"
if STATIC.exists():
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="frontend")


class DesktopApi:
    def open_notice_window(self, url: str) -> bool:
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
            return False
        webview.create_window('原通知网页', url, width=1200, height=850, text_select=True)
        return True
    def open_external(self, url: str) -> bool:
        try:
            parsed = urlsplit(url)
        except ValueError:
            return False
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        return webbrowser.open(url)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def monitor() -> None:
    time.sleep(15)
    while True:
        try:
            scan_all()
        except Exception:
            pass  # Individual source failures are stored for display in settings.
        time.sleep(30 * 60)


if __name__ == "__main__":
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CampusRadar.Desktop")
    port = free_port()
    threading.Thread(target=lambda: uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    threading.Thread(target=monitor, daemon=True).start()
    time.sleep(0.7)
    webview.create_window("校务雷达 · CampusRadar", f"http://127.0.0.1:{port}", width=1440, height=900,
                          min_size=(1000, 650), background_color="#151516", text_select=True,
                          js_api=DesktopApi())
    webview.start(icon=str(BASE / "campusradar.ico"))
