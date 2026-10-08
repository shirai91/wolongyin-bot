# AutoNgoaLong v2 (Python rewrite)

A Python rewrite of AutoNgoaLong v1.8.0, a bot for the Flash webgame **Ngọa Long** on the private server ngoalongviet.com, played in the **Coowon** browser on Windows. It clicks in the background with `PostMessage`, captures with `PrintWindow`, finds the 1000×600 game canvas by itself, and recognises screens with OpenCV templates. Every wait is time-limited and can be stopped.

- Users: read the Vietnamese guide, [docs/user-guide-vi.md](docs/user-guide-vi.md). It ships with the build as `HUONG-DAN.md`.
- Design: [docs/06-rewrite-design.md](docs/06-rewrite-design.md) (stack, module layout, interfaces, roadmap, implementation status). All design docs are listed in [docs/README.md](docs/README.md).

## Developer quick start

Requirements: Windows 10/11 64-bit, Python 3.12 or newer (the project is developed on 3.14 through the `py` launcher). The UI language is Vietnamese.

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m ngoalong            # start the app
.venv\Scripts\python.exe -m ngoalong --debug    # start with debug mode (overlay, step PNGs) on
.venv\Scripts\python.exe -m ngoalong --selftest # offline golden checks, exit code 0/1
```

Command line of `python -m ngoalong` (and of `AutoNgoaLong.exe`):

| Option | Effect |
| --- | --- |
| `--settings PATH` | `settings.toml` to use (default: next to the app) |
| `--ini PATH` | v1.8.0 `NgoaLong_data.ini` imported once, when no `settings.toml` exists yet |
| `--debug` | Start with debug mode on |
| `--selftest` | Run the offline checks (profile, features, golden screenshots, offscreen main window) and exit 0 (passed) or 1 (failed) |
| `--golden PATH` | `--selftest`: golden `expected.yaml` (default: the bundled set in the build, `tests/fixtures/screens/expected.yaml` from source) |
| `--report PATH` | `--selftest`: also write the report to a file (the windowed build always writes `selftest-report.txt` next to the exe) |
| `--no-ui` | `--selftest`: skip the main window check |

To try it against the game: open Coowon on the game page (click **Run this time** on the "Adobe Flash Player was blocked" bar), start the app, press **Kiểm tra**. The status line should read `Khung game: … 1000×600 ×1.00 - tin cậy` and name the current scene.

## Layout

```text
ngoalong/                  the package (docs/06 "Module layout")
  app.py                   entry point, AppServices (dependency wiring), command line
  paths.py                 APP_ROOT (project root / exe folder) and BUNDLE_ROOT (_internal)
  selftest.py              --selftest checks and the build's golden bundle
  config/settings.py       pydantic settings saved as settings.toml, one-time INI import
  core/                    GameWindow, CanvasLocator, SceneDetector, Matcher, CoordMap, capture/input
    drivers/               win32.py (MVP), file.py (offline replay), fake.py (recording input), cdp.py (stub)
  engine/                  RunContext, Task, registry, TaskRunner, RunnerManager, scheduler, history
  features/                one module per feature, registered with @register
  debug/                   DebugFrame model, renderer, overlay window, sinks
  ui/                      main window, generated feature forms, tabs/ (one module per tab), widgets/
  tools/                   annotate.py (annotated screenshots), template_tool.py
  hotkeys.py  notify.py
profiles/ngoalongviet/     data, editable without rebuilding
  coords/*.yaml            canvas-relative click offsets at 1000x600, one file per feature
  scenes/*.yaml            scene signatures and objects
  templates/**             template PNGs (+ optional .mask.png)
  locator.yaml             canvas anchors
  template_crops.yaml      reproducible template cuts (tools/cut_templates.py)
tests/                     pytest suite; fixtures/screens/expected.yaml = golden canvas + scene per screenshot
docs/                      design documents, screen catalogue, user guide, reference screenshots
tools/                     cut_templates.py; live/livectl.cs (reference Win32 find/capture/click)
AutoNgoaLong.spec          PyInstaller spec (one-folder build)
build.ps1                  build script
```

Extension points: a feature is one file in `ngoalong/features/` with `@register`, its offsets go in `profiles/ngoalongviet/coords/<feature>.yaml`, its screens in `profiles/ngoalongviet/scenes/<name>.yaml`, and the UI form is generated from its `Params` model. A UI tab is one module in `ngoalong/ui/tabs/` with `@register_tab`. Features, tabs and widgets are discovered with `pkgutil` at run time.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest                     # offline suite (about a minute)
.venv\Scripts\python.exe -m pytest tests\test_scenes.py # one file
$env:NGOALONG_LIVE = "1"; .venv\Scripts\python.exe -m pytest tests\test_core_live.py tests\test_engine_live.py
```

- Offline tests replay stored screenshots with `FileCapturer` and record clicks with `RecordingInputDriver`; they never need the game and never click on the desktop. Qt tests run with `QT_QPA_PLATFORM=offscreen`.
- Golden tests (`tests/test_locator.py`, `tests/test_scenes.py`) check the canvas rectangle (±2 px) and the scene name for every screenshot listed in `tests/fixtures/screens/expected.yaml`.
- Live tests are skipped unless `NGOALONG_LIVE=1` and a Coowon "Ngọa Long" window is open. They capture and probe, they do not run features.
- Annotated screenshots for a visual check: `.venv\Scripts\python.exe -m ngoalong.tools.annotate "docs/game screenshots/*.png" --out build/annotated`.

## Build

```powershell
.\build.ps1                 # venv + deps if missing, pytest, PyInstaller, then AutoNgoaLong.exe --selftest
.\build.ps1 -SkipTests      # skip pytest
.\build.ps1 -SkipSelftest   # skip the self-test of the built exe
```

Output is a one-folder build in `dist\AutoNgoaLong\`:

| Path | Contents |
| --- | --- |
| `AutoNgoaLong.exe` | Windowed app; `--selftest` runs the offline checks on the bundled golden screenshots |
| `_internal\` | Python runtime, Qt, OpenCV, the `ngoalong` package, `selftest\` (15 golden screenshots + `expected.yaml`) |
| `profiles\` | Copied from the source tree, next to the exe, so offsets, scenes and templates stay editable |
| `HUONG-DAN.md`, `VERSION.txt` | The Vietnamese user guide and the version |

At run time the build reads and writes `settings.toml`, `NgoaLong_data.ini` (one-time import), `logs\` (run folders and `app.log`) and `data\` (history, routines) next to the exe; see `ngoalong/paths.py`. The build script exits non-zero when pytest, PyInstaller or the self-test fails; the self-test report is saved to `build\selftest-report.txt`. Zip `dist\AutoNgoaLong\` to distribute it.

## Licence and policy

The original v1.8.0 code has no licence (the author was asked). The target server allows bots. See docs/06 "Decisions".
