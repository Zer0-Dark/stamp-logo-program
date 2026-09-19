"""Session state: what has been placed, what has been rendered, and resuming.

Positions are stored normalised (0..1) so they survive any change of preview
size, and the whole session is a single JSON sidecar so a crash or a closed
window never costs more than the current photo.
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import stamper

STATE_DIRNAME = ".logostamper"


def _as_marks(entry: dict) -> dict:
    """Accept both the current shape and single-logo sessions written earlier."""
    if "marks" in entry or entry.get("skip"):
        return entry
    if "x" in entry:
        mark = {"logo": 0, "x": entry["x"], "y": entry["y"]}
        if entry.get("scale"):
            mark["scale"] = entry["scale"]
        return {"marks": [mark]}
    return entry


class Session:
    def __init__(
        self,
        photo_dir: Path,
        logo_paths: list[Path] | Path,
        out_dir: Path,
        batch_size: int = 10,
        global_scale: float = 0.10,
        opacity: float = 1.0,
        suffix: str = "_stamped",
    ):
        self.photo_dir = Path(photo_dir)
        if isinstance(logo_paths, (str, Path)):
            logo_paths = [logo_paths]
        self.logo_paths = [Path(p) for p in logo_paths]
        self.out_dir = Path(out_dir)
        self.batch_size = batch_size
        self.global_scale = global_scale
        self.opacity = opacity
        self.suffix = suffix

        self.state_dir = self.photo_dir / STATE_DIRNAME
        self.cache_dir = self.state_dir / "previews"
        self.state_file = self.state_dir / "session.json"

        self.files = stamper.list_images(self.photo_dir)
        self.names = [p.name for p in self.files]
        self.by_name = {p.name: p for p in self.files}

        # name -> {"marks": [{"logo": int, "x": float, "y": float,
        #                      "scale": float|None}, ...], "skip": bool}
        self.positions: dict[str, dict] = {}
        self.rendered: set[str] = set()
        self.failed: dict[str, str] = {}
        self.cursor = 0
        # True when an earlier run of this folder was picked up, in which case
        # its saved size/opacity win over whatever the setup screen showed.
        self.resumed = False

        self._lock = threading.Lock()
        self._dirty = 0
        self._logos = [stamper.load_logo(p) for p in self.logo_paths]
        self.logo_aspects = [lg.height / lg.width for lg in self._logos]

        # Capped deliberately: each worker holds a full RGBA copy of a photo,
        # which is ~48 MB for a 24MP image. Twelve of those at once would be
        # unkind to a laptop, and rendering already outruns the clicking.
        self._pool = ThreadPoolExecutor(max_workers=min(4, max(2, os.cpu_count() or 4)))
        self._render_lock = threading.Lock()
        self._rendering: set[str] = set()

        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._hide_state_dir()
        self.load()

        # Warm previews in the background so clicking never waits on disk.
        threading.Thread(target=self._warm_previews, daemon=True).start()

    # ---------------------------------------------------------------- state

    def _hide_state_dir(self) -> None:
        if os.name == "nt":
            try:
                import ctypes

                ctypes.windll.kernel32.SetFileAttributesW(str(self.state_dir), 0x02)
            except Exception:
                pass

    def load(self) -> None:
        if not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        same_logos = data.get("logo_paths") == [str(p) for p in self.logo_paths]
        self.resumed = bool(data.get("positions"))
        if same_logos:
            self.global_scale = data.get("global_scale", self.global_scale)
            self.opacity = data.get("opacity", self.opacity)
        self.batch_size = data.get("batch_size", self.batch_size)
        self.suffix = data.get("suffix", self.suffix)
        self.positions = {
            k: _as_marks(v)
            for k, v in data.get("positions", {}).items()
            if k in self.by_name
        }
        self.rendered = {n for n in data.get("rendered", []) if n in self.by_name}
        # Trust the filesystem over the log, in both directions: a deleted
        # output must be redone, and one written after the last checkpoint
        # must not be redone.
        self.rendered = {
            n for n, pos in self.positions.items()
            if not pos.get("skip") and pos.get("marks") and self.output_for(n).exists()
        }
        self.cursor = min(data.get("cursor", 0), max(0, len(self.files) - 1))

    def save(self, force: bool = False) -> None:
        """Checkpoint. Writes every `batch_size` placements, or on demand."""
        with self._lock:
            self._dirty += 0 if force else 1
            if not force and self._dirty < self.batch_size:
                return
            self._dirty = 0
            data = {
                "version": 1,
                "photo_dir": str(self.photo_dir),
                "logo_paths": [str(p) for p in self.logo_paths],
                "out_dir": str(self.out_dir),
                "batch_size": self.batch_size,
                "global_scale": self.global_scale,
                "opacity": self.opacity,
                "suffix": self.suffix,
                "cursor": self.cursor,
                "positions": self.positions,
                "rendered": sorted(self.rendered),
                "saved_at": time.time(),
            }
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, self.state_file)

    # ------------------------------------------------------------- placing

    def output_for(self, name: str) -> Path:
        src = self.by_name[name]
        return self.out_dir / f"{src.stem}{self.suffix}{src.suffix}"

    def place(self, name: str, marks: list[dict]) -> None:
        """Replace every mark on a photo. An empty list clears it."""
        clean = []
        for m in marks:
            entry = {
                "logo": max(0, min(int(m.get("logo", 0)), len(self._logos) - 1)),
                "x": round(float(m["x"]), 5),
                "y": round(float(m["y"]), 5),
            }
            if m.get("scale"):
                entry["scale"] = round(float(m["scale"]), 5)
            clean.append(entry)
        if clean:
            self.positions[name] = {"marks": clean}
            self.rendered.discard(name)
            self.save()
            self.queue_render(name)
        else:
            self.positions.pop(name, None)
            self._discard_output(name)
            self.save()

    def skip(self, name: str) -> None:
        self.positions[name] = {"skip": True}
        self._discard_output(name)
        self.save()

    def clear(self, name: str) -> None:
        self.positions.pop(name, None)
        self._discard_output(name)
        self.save()

    def _discard_output(self, name: str) -> None:
        """Drop a stamped file that should no longer exist.

        Going back to a photo and skipping it -- or clearing its logos -- has to
        remove the copy written earlier, otherwise a stale stamped photo is left
        in the output folder.
        """
        self.rendered.discard(name)
        try:
            self.output_for(name).unlink(missing_ok=True)
        except OSError:
            pass

    def marks_for(self, name: str) -> list[dict]:
        return self.positions.get(name, {}).get("marks", [])

    # ------------------------------------------------------------ rendering

    def queue_render(self, name: str) -> None:
        """Render in the background so the user never waits between photos."""
        with self._render_lock:
            if name in self._rendering:
                return
            self._rendering.add(name)
        self._pool.submit(self._render_one, name)

    def _render_one(self, name: str) -> None:
        try:
            pos = self.positions.get(name)
            if not pos or pos.get("skip") or not pos.get("marks"):
                return
            stamper.stamp(
                self.by_name[name],
                self.out_dir,
                pos["marks"],
                self._logos,
                self.global_scale,
                opacity=self.opacity,
                suffix=self.suffix,
            )
            self.rendered.add(name)
            self.failed.pop(name, None)
        except Exception as exc:  # a single bad file must not stall the run
            self.failed[name] = f"{type(exc).__name__}: {exc}"
        finally:
            with self._render_lock:
                self._rendering.discard(name)

    def render_all(self) -> int:
        """Queue every placed-but-not-yet-written photo. Returns the count."""
        pending = [
            n
            for n, p in self.positions.items()
            if not p.get("skip") and p.get("marks") and n not in self.rendered
        ]
        for name in pending:
            self.queue_render(name)
        return len(pending)

    def rerender_all(self) -> int:
        """Force a rewrite of everything, e.g. after the global size changed."""
        self.rendered.clear()
        return self.render_all()

    # -------------------------------------------------------------- previews

    def _warm_previews(self) -> None:
        for path in self.files:
            try:
                stamper.make_preview(path, self.cache_dir)
            except Exception:
                continue

    def preview(self, name: str) -> Path:
        return stamper.make_preview(self.by_name[name], self.cache_dir)

    # --------------------------------------------------------------- status

    def stats(self) -> dict:
        placed = sum(1 for p in self.positions.values() if p.get("marks"))
        skipped = sum(1 for p in self.positions.values() if p.get("skip"))
        with self._render_lock:
            in_flight = len(self._rendering)
        return {
            "total": len(self.files),
            "placed": placed,
            "skipped": skipped,
            "rendered": len(self.rendered),
            "rendering": in_flight,
            "failed": self.failed,
            "remaining": len(self.files) - placed - skipped,
        }

    def close(self) -> None:
        self.save(force=True)
        self._pool.shutdown(wait=False)
