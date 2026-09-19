"""Assemble a portable Windows bundle from any OS (no compilation needed).

Downloads Python's official Windows embeddable runtime plus the Windows Pillow
wheel and lays them out next to the app, so the customer needs nothing
installed -- just unzip and double-click.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PY_VERSION = "3.11.9"
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
ROOT = Path(__file__).parent
BUILD = ROOT / "build_portable"
OUT = BUILD / "LogoStamper"


def fetch(url: str) -> bytes:
    print(f"  downloading {url.rsplit('/', 1)[-1]} ...")
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def main() -> int:
    shutil.rmtree(BUILD, ignore_errors=True)
    OUT.mkdir(parents=True)
    runtime = OUT / "runtime"
    runtime.mkdir(exist_ok=True)

    print("1. Windows Python runtime")
    with zipfile.ZipFile(io.BytesIO(fetch(PY_URL))) as z:
        z.extractall(runtime)

    # The ._pth file controls sys.path for embeddable Python. Point it at the
    # app folder and at site-packages so Pillow is importable.
    pth = next(runtime.glob("python*._pth"))
    path_config = (
        f"{pth.stem}.zip\n"
        ".\n"
        "Lib\\site-packages\n"
        "..\\app\n"
        "\n"
        "import site\n"
    )
    pth.write_text(path_config)
    print(f"  configured {pth.name}")

    # Rename the interpreter so the taskbar and Task Manager show the app's
    # name instead of "python.exe". Embeddable Python looks for a ._pth named
    # after the executable first, then after the DLL, so ship both.
    exe = runtime / "LogoStamper.exe"
    (runtime / "python.exe").rename(exe)
    (runtime / "LogoStamper._pth").write_text(path_config)
    icon = ROOT / "logostamper" / "static" / "icon.ico"
    if icon.exists():
        shutil.copy(icon, OUT / "icon.ico")
    print(f"  renamed python.exe -> {exe.name}")

    print("2. Pillow wheel for Windows")
    site = runtime / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    wheels = BUILD / "wheels"
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "pillow",
         "--only-binary=:all:", "--platform", "win_amd64",
         "--python-version", "311", "--dest", str(wheels), "-q"],
        check=True,
    )
    wheel = next(wheels.glob("*.whl"))
    with zipfile.ZipFile(wheel) as z:
        z.extractall(site)
    print(f"  installed {wheel.name}")

    print("3. Application")
    app = OUT / "app"
    app.mkdir(exist_ok=True)
    shutil.copytree(ROOT / "logostamper", app / "logostamper",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "run.py", app / "run.py")

    # A single, obviously-named launcher. The console window is deliberate:
    # it is how the user sees the program is running, and closing it quits.
    (OUT / "START HERE - Logo Stamper.bat").write_text(
        "@echo off\r\n"
        "title Logo Stamper - keep this window open\r\n"
        "cd /d \"%~dp0\"\r\n"
        "echo Starting Logo Stamper...\r\n"
        "echo.\r\n"
        "echo Keep this window open while you work.\r\n"
        "echo Closing it will close the program.\r\n"
        "echo.\r\n"
        "runtime\\LogoStamper.exe app\\run.py\r\n"
        "if errorlevel 1 (\r\n"
        "  echo.\r\n"
        "  echo Something went wrong - please show this window to your developer.\r\n"
        "  pause\r\n"
        ")\r\n"
    )
    shutil.copy(ROOT / "README.md", OUT / "README.md")
    # The guide doubles as a standalone file: the client can open or print it
    # without starting the program.
    shutil.copy(ROOT / "logostamper" / "static" / "guide.html",
                OUT / "Instructions.html")

    print("4. Zipping")
    archive = shutil.make_archive(str(BUILD / "LogoStamper-windows"), "zip", OUT.parent, OUT.name)
    size = Path(archive).stat().st_size / 1024 / 1024
    print(f"\nDone: {archive}  ({size:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
