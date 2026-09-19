"""Local HTTP server. Runs on 127.0.0.1 only; nothing leaves the machine."""
from __future__ import annotations

import json
import mimetypes
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.request
from urllib.parse import parse_qs, urlparse

from .session import Session

def _static_dir() -> Path:
    """Locate the UI files, both when run from source and from a PyInstaller exe."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "logostamper" / "static"
    return Path(__file__).parent / "static"


STATIC = _static_dir()
state: dict = {"session": None}
shutdown_event = threading.Event()


# --------------------------------------------------------------- native dialogs

def pick_path(kind: str, title: str) -> list[str]:
    """Open a native file/folder dialog and return the chosen path(s).

    A web page cannot raise a native picker, and a browser never reveals an
    absolute path, so a short-lived child process does it instead.
    """
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--pick", kind, title]
    else:
        cmd = [sys.executable, "-m", "logostamper", "--pick", kind, title]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=1800,
            cwd=str(Path(__file__).parent.parent),
        )
        return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    except Exception:
        return []


def run_picker(kind: str, title: str) -> None:
    """Child-process entry point: print one chosen path per line, then exit."""
    paths = _pick_windows(kind, title) if os.name == "nt" else _pick_tk(kind, title)
    for path in paths:
        print(path)


# PowerShell drives the real Explorer dialog -- the one with an address bar,
# a sidebar and paste-a-path support. The embeddable Python runtime used by
# the portable build has no tkinter, so this is also the only option there.
#
# Folders use the documented OpenFileDialog trick (ValidateNames off plus a
# placeholder file name) rather than FolderBrowserDialog, which is the old
# tree-view widget with no address bar.
_PS_COMMON = """
Add-Type -AssemblyName System.Windows.Forms | Out-Null
$owner = New-Object System.Windows.Forms.Form -Property @{
  TopMost = $true; ShowInTaskbar = $false; Opacity = 0
}
"""

_PS_FOLDER = """
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = '{title}'
$d.ValidateNames = $false
$d.CheckFileExists = $false
$d.CheckPathExists = $true
$d.FileName = 'Open this folder'
$d.Filter = 'Folders|*.none'
if ($d.ShowDialog($owner) -eq 'OK') {{
  Write-Output (Split-Path -Parent $d.FileName)
}}
"""

_PS_FILE = """
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = '{title}'
$d.Multiselect = ${multi}
$d.Filter = 'Logo image|*.png;*.webp;*.gif;*.jpg;*.jpeg|All files|*.*'
if ($d.ShowDialog($owner) -eq 'OK') {{
  foreach ($f in $d.FileNames) {{ Write-Output $f }}
}}
"""


def _pick_windows(kind: str, title: str) -> list[str]:
    # The title is interpolated into a PowerShell single-quoted string.
    title = title.replace("'", "").replace("\n", " ")[:120]
    if kind == "folder":
        script = _PS_COMMON + _PS_FOLDER.format(title=title)
    else:
        script = _PS_COMMON + _PS_FILE.format(
            title=title, multi="true" if kind == "files" else "false"
        )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True, text=True, timeout=1800,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    except Exception:
        return []


def _pick_tk(kind: str, title: str) -> list[str]:
    """Development fallback for non-Windows machines."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return []
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    types = [("Logo image", "*.png *.webp *.gif *.jpg *.jpeg"), ("All files", "*.*")]
    if kind == "folder":
        got = filedialog.askdirectory(title=title)
        paths = [got] if got else []
    elif kind == "files":
        paths = list(filedialog.askopenfilenames(title=title, filetypes=types))
    else:
        got = filedialog.askopenfilename(title=title, filetypes=types)
        paths = [got] if got else []
    root.destroy()
    return [p for p in paths if p]


# ---------------------------------------------------------------------- routing

class Handler(BaseHTTPRequestHandler):
    server_version = "LogoStamper"

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def handle_one_request(self):
        # A dropped connection must not print a scary traceback in the window
        # the user is told to keep open.
        try:
            super().handle_one_request()
        except OSError:
            self.close_connection = True

    def handle_error(self, request, client_address):
        pass

    # -- helpers

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        # The browser routinely walks away from a request -- a reload, or a
        # long-running picker the user gave up on. On Windows that surfaces as
        # WinError 10053 (ConnectionAbortedError). It is not an error worth a
        # traceback, so swallow every disconnect the same way.
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data).encode(), "application/json")

    def _file(self, path: Path, ctype: str | None = None) -> None:
        if not path.exists():
            return self._json({"error": "not found"}, 404)
        ctype = ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(200, path.read_bytes(), ctype)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    @property
    def sess(self) -> Session | None:
        return state["session"]

    # -- GET

    def do_GET(self):
        url = urlparse(self.path)
        route, q = url.path, parse_qs(url.query)

        if route == "/":
            return self._file(STATIC / "index.html", "text/html; charset=utf-8")
        if route.startswith("/static/"):
            target = (STATIC / route[len("/static/"):]).resolve()
            if STATIC.resolve() in target.parents:
                return self._file(target)
            return self._json({"error": "forbidden"}, 403)

        if route == "/api/state":
            s = self.sess
            if not s:
                return self._json({"open": False})
            return self._json({
                "open": True,
                "photo_dir": str(s.photo_dir),
                "out_dir": str(s.out_dir),
                "logos": [
                    {"name": lp.name, "aspect": a}
                    for lp, a in zip(s.logo_paths, s.logo_aspects)
                ],
                "global_scale": s.global_scale,
                "opacity": s.opacity,
                "batch_size": s.batch_size,
                "suffix": s.suffix,
                "cursor": s.cursor,
                "names": s.names,
                "positions": s.positions,
                "rendered": sorted(s.rendered),
                "stats": s.stats(),
            })

        if route == "/api/progress":
            return self._json(self.sess.stats() if self.sess else {})

        if route == "/api/preview":
            s = self.sess
            name = (q.get("name") or [""])[0]
            if not s or name not in s.by_name:
                return self._json({"error": "unknown photo"}, 404)
            try:
                return self._file(s.preview(name), "image/jpeg")
            except Exception as exc:
                return self._json({"error": str(exc)}, 500)

        if route == "/api/logo":
            s = self.sess
            if not s:
                return self._json({"error": "no session"}, 404)
            try:
                idx = int((q.get("i") or ["0"])[0])
            except ValueError:
                idx = 0
            if not 0 <= idx < len(s.logo_paths):
                return self._json({"error": "unknown logo"}, 404)
            return self._file(s.logo_paths[idx])

        if route == "/api/pick":
            kind = (q.get("kind") or ["folder"])[0]
            title = (q.get("title") or ["Choose"])[0]
            paths = pick_path(kind, title)
            return self._json({"paths": paths, "path": paths[0] if paths else ""})

        return self._json({"error": "not found"}, 404)

    # -- POST

    def do_POST(self):
        route = urlparse(self.path).path
        body = self._body()

        if route == "/api/open":
            try:
                photo_dir = Path(body["photo_dir"]).expanduser()
            except KeyError:
                return self._json({"error": "photo_dir is required"}, 400)
            raw_logos = body.get("logo_paths") or (
                [body["logo_path"]] if body.get("logo_path") else []
            )
            logo_paths = [Path(p).expanduser() for p in raw_logos]
            if not logo_paths:
                return self._json({"error": "Choose at least one logo"}, 400)
            if not photo_dir.is_dir():
                return self._json({"error": f"Folder not found: {photo_dir}"}, 400)
            for lp in logo_paths:
                if not lp.is_file():
                    return self._json({"error": f"Logo not found: {lp}"}, 400)

            out_dir = Path(body["out_dir"]).expanduser() if body.get("out_dir") else \
                photo_dir.parent / f"{photo_dir.name}_stamped"
            if out_dir.resolve() == photo_dir.resolve():
                return self._json({"error": "Output folder must differ from the photo folder"}, 400)

            if self.sess:
                self.sess.close()
            try:
                s = Session(
                    photo_dir, logo_paths, out_dir,
                    batch_size=int(body.get("batch_size", 10)),
                    global_scale=float(body.get("global_scale", 0.10)),
                    opacity=float(body.get("opacity", 1.0)),
                    suffix=body.get("suffix", "_stamped"),
                )
            except Exception as exc:
                return self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)
            if not s.files:
                return self._json({"error": "No images found in that folder"}, 400)
            state["session"] = s
            return self._json({
                "ok": True,
                "count": len(s.files),
                "out_dir": str(out_dir),
                "resumed": s.resumed,
                "already_done": len(s.positions),
                "global_scale": s.global_scale,
                "opacity": s.opacity,
            })

        s = self.sess
        if not s:
            return self._json({"error": "no session"}, 400)

        if route == "/api/place":
            name = body.get("name")
            if name not in s.by_name:
                return self._json({"error": "unknown photo"}, 400)
            marks = body.get("marks")
            if marks is None:  # single-mark form
                marks = [{"logo": body.get("logo", 0), "x": body["x"],
                          "y": body["y"], "scale": body.get("scale")}]
            try:
                s.place(name, marks)
            except (KeyError, TypeError, ValueError) as exc:
                return self._json({"error": f"bad marks: {exc}"}, 400)
            return self._json({"ok": True, "stats": s.stats()})

        if route == "/api/skip":
            s.skip(body["name"])
            return self._json({"ok": True, "stats": s.stats()})

        if route == "/api/clear":
            s.clear(body["name"])
            return self._json({"ok": True, "stats": s.stats()})

        if route == "/api/cursor":
            s.cursor = int(body.get("cursor", 0))
            return self._json({"ok": True})

        if route == "/api/settings":
            changed = False
            if "global_scale" in body:
                new = float(body["global_scale"])
                changed = abs(new - s.global_scale) > 1e-6
                s.global_scale = new
            if "opacity" in body:
                new = float(body["opacity"])
                changed = changed or abs(new - s.opacity) > 1e-6
                s.opacity = new
            if "batch_size" in body:
                s.batch_size = max(1, int(body["batch_size"]))
            s.save(force=True)
            # Photos already written used the old size, so redo them.
            queued = s.rerender_all() if changed and body.get("rerender", True) else 0
            return self._json({"ok": True, "requeued": queued})

        if route == "/api/render":
            return self._json({"ok": True, "queued": s.render_all()})

        if route == "/api/save":
            s.save(force=True)
            return self._json({"ok": True})

        if route == "/api/quit":
            s.close()
            self._json({"ok": True})
            shutdown_event.set()
            return

        return self._json({"error": "not found"}, 404)


# ------------------------------------------------------------------- bootstrap

def already_running(port: int = 8753) -> bool:
    """True when another copy of Logo Stamper already holds the port.

    Double-clicking the launcher twice would otherwise start a second program
    on a different port, with two sessions writing into the same output folder.
    """
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/state", timeout=1.5
        ) as res:
            if res.headers.get("Server", "").startswith("LogoStamper"):
                return True
            json.loads(res.read())
            return True
    except Exception:
        return False


def free_port(preferred: int = 8753) -> int:
    for port in (preferred, 0):
        with socket.socket() as sock:
            # Without SO_REUSEADDR a socket left in TIME_WAIT by the previous
            # run makes the preferred port look busy, and the app drifts onto a
            # random port on every restart.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return sock.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--pick":
        run_picker(argv[1], argv[2] if len(argv) > 2 else "Choose")
        return 0

    if already_running(8753):
        url = "http://127.0.0.1:8753/"
        print("\n  Logo Stamper is already running -- opening it again.")
        print(f"  If nothing opens, go to: {url}\n")
        webbrowser.open(url)
        return 0

    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True

    print(f"\n  Logo Stamper is running.\n  If your browser did not open, go to: {url}\n")
    print("  Keep this window open while you work. Close it when you are done.\n")
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    try:
        shutdown_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if state["session"]:
            state["session"].close()
        httpd.shutdown()
    print("  Stopped. All work has been saved.")
    return 0
