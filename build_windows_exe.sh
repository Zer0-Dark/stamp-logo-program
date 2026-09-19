#!/usr/bin/env bash
# Build a real Windows LogoStamper.exe from Linux/macOS using PyInstaller
# under Wine in Docker. PyInstaller cannot cross-compile, so this runs the
# genuine Windows toolchain inside a container.
#
#   ./build_windows_exe.sh
#
# Result: dist/LogoStamper.exe
set -euo pipefail

IMAGE="tobix/pywine:3.12"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> Using $IMAGE"
docker pull -q "$IMAGE" >/dev/null

docker run --rm -v "$HERE:/src" -w /src "$IMAGE" bash -lc '
  set -e
  echo "==> Installing build deps into the Wine Python"
  wine python -m pip install --quiet --upgrade pip
  wine python -m pip install --quiet pillow pyinstaller

  echo "==> Building"
  wine python -m PyInstaller \
    --onefile \
    --name LogoStamper \
    --icon logostamper/static/icon.ico \
    --add-data "logostamper/static;logostamper/static" \
    --collect-submodules PIL \
    --exclude-module tkinter \
    --exclude-module numpy \
    --exclude-module pytest \
    --noconfirm --clean \
    run.py

  echo "==> Done"
  ls -la dist/
'

echo
echo "==========================================================="
echo " Built: $HERE/dist/LogoStamper.exe"
echo "==========================================================="
