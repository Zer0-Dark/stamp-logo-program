# Logo Stamper

Place a logo on hundreds of photos, one click each.

Made by [Zer0-Dark](https://github.com/Zer0-Dark).

Built for a non-technical user on Windows: one file to double-click, no Python,
no command line, no internet. Originals are never modified and nothing is ever
uploaded anywhere — everything happens on the local machine.

---

## For the person using it

1. Unzip **LogoStamper-windows.zip** anywhere (Desktop is fine).
   Unzip it properly — do not run it from inside the zip preview.
2. Double-click **START HERE - Logo Stamper.bat**.
   A black window appears and your browser opens. **Leave the black window open**
   while you work — it is the program itself.
3. Choose the folder of photos, then choose **one or more logos** — either
   `Ctrl`-click several in the dialog, or press "Add logo…" once per logo, as
   they accumulate. Press **Start**.
4. For each photo, **click where the logo should go**. It jumps straight to the
   next photo. Finished photos are saved as you go.

   With more than one logo, clicking places the highlighted logo and moves to
   the next logo on the *same* photo; once every logo is down it moves on to
   the next photo. The chips in the toolbar show which logo is active and
   which are already placed.
5. Press **Finish** when done. The stamped photos are in a new folder named
   after your photo folder with `_stamped` on the end.

### Going fast

Clicking is fine, but the number keys are faster — they match a numpad layout:

```
 7  8  9      top-left     top-centre     top-right
 4  5  6      left         centre         right
 1  2  3      bottom-left  bottom-centre  bottom-right
```

| Key | Does |
|---|---|
| Click | Place the logo there, go to next photo |
| `1`–`9` | Place at one of nine fixed spots, go to next photo |
| `Space` | Same spots as the previous photo (all logos at once) |
| `Tab` | Switch to the next logo, if you chose more than one |
| `Delete` | Remove the current logo from this photo |
| `S` | Skip this photo (no logo) |
| `Backspace` | Go back a photo to redo it |
| `←` `→` | Move between photos without changing anything |
| `+` `−` | Logo bigger / smaller, for **all** photos |
| Size / Opacity sliders | Change every logo live; already-written photos are redone automatically |
| `Shift` + scroll | Logo bigger / smaller, for **this photo only** |
| `?` | Show the shortcuts |

### If it gets closed by accident

Just start it again and pick the same folder. It remembers every photo already
done and carries on from where it stopped.

---

## How it works

Two things run on the user's own PC and talk to each other over `127.0.0.1`
(the machine's own loopback address — not reachable from the network):

- a small Python web server that reads and writes the photo files
- the browser, which is only the window

The browser is sandboxed and cannot write full-quality JPEGs to a folder of
1000 files, so the Python side does the image work.

### Speed

Clicking and rendering are decoupled, so the user never waits:

- while annotating, they see a cached **preview** (1500px), generated ahead of
  time by a background thread
- the moment a position is set, the **full-resolution** render is queued to a
  thread pool and happens in the background

Measured on a 12-core machine, 24MP photos: 0.65s per photo single-threaded,
so 1000 photos finish in roughly 1.5 minutes of background work — far faster
than a person can click through them.

### Quality

- The source is never downscaled; output dimensions always equal the input.
- JPEG is re-encoded at quality 96 with `subsampling=0` (no chroma
  subsampling), which is visually lossless.
- **EXIF** and the **ICC colour profile** are carried across, so colours and
  camera metadata survive.
- EXIF rotation is applied to both the preview and the output, so what the user
  clicks is what gets saved.
- Writes go to a temporary file and are then atomically renamed, so an
  interrupted run never leaves a half-written photo.

### Multiple logos

Each logo is placed independently on every photo, and each keeps its own
position and optional per-photo size. Marks are stored as
`{"logo": index, "x": .., "y": .., "scale": ..}`, so a session made with one
logo still opens: single-logo entries are migrated to the new shape on load.

### Logo sizing

The photos are not a fixed size, so the logo is sized against the photo's
**geometric mean** — `sqrt(width × height)` — rather than its width. This keeps
the logo at the same visual weight on landscape and portrait shots alike:

| Photo | Logo width at 10% | as % of width |
|---|---|---|
| 4000×3000 | 346 px | 8.7% |
| 3000×4000 | 346 px | 11.5% |
| 6000×4000 | 490 px | 8.2% |
| 1600×1600 | 160 px | 10.0% |

Sizing by width instead would make the logo look noticeably smaller on every
portrait photo. The default is 10%; the slider covers 2–40%.

### Checkpoints and resuming

State lives in a hidden `.logostamper\session.json` inside the photo folder:
normalised positions (`0..1`), the settings, and which photos are done. It is
flushed every N placements (default 10, configurable on the setup screen).

On restart, the log is cross-checked against the output folder in both
directions — a deleted output is redone, and an output written after the last
checkpoint is not redone.

---

## For the developer

```bash
pip install -r requirements.txt
python run.py
```

### Building for Windows

Two options.

**Portable bundle (recommended, builds on any OS):**

```bash
python build_portable.py
```

Downloads Python's official Windows embeddable runtime and the Windows Pillow
wheel, and lays them out next to the app. Produces
`build_portable/LogoStamper-windows.zip` (~18 MB). The customer unzips it and
double-clicks one `.bat` — nothing to install.

This is preferred over a PyInstaller exe because it needs no Windows machine to
build, it is a third of the size, and unsigned one-file PyInstaller exes are
frequently quarantined by Windows Defender and blocked by SmartScreen.

**Single exe, built from Linux/macOS via Wine in Docker:**

```bash
./build_windows_exe.sh
```

Produces `dist/LogoStamper.exe`. PyInstaller cannot cross-compile, so this runs
the genuine Windows toolchain inside a container (`tobix/pywine`). Needs Docker
and roughly 2 GB of image download the first time.

**Single exe, on a Windows machine:**

```
build_windows.bat
```

Same result, using a locally installed Python 3.10+.

> Note on antivirus: unsigned one-file PyInstaller executables are fairly often
> flagged by Windows Defender or blocked by SmartScreen ("Windows protected
> your PC" → *More info* → *Run anyway*). The portable bundle does not have
> this problem. Code-signing removes it, at roughly $200/year for a
> certificate.

### Layout

```
run.py                     entry point
logostamper/
  app.py                   HTTP server, routes, native folder dialogs
  session.py               state, checkpointing, resume, render queue
  stamper.py               compositing, preview generation, encoding
  static/                  index.html, app.js, style.css
```

### Notes

- Only dependency is **Pillow**. The server is `http.server` from the standard
  library, which keeps the exe small and the PyInstaller build reliable.
- A web page cannot open a native "choose folder" dialog, so `/api/pick` shells
  out to a short-lived **child process**. On Windows that is PowerShell driving
  `System.Windows.Forms` (the embeddable runtime has no tkinter, and these are
  the dialogs the user already knows); elsewhere it falls back to tkinter for
  development.
- The console window is deliberate: it is how the user sees the program is
  running, and closing it quits cleanly.
# stamp-logo-program
