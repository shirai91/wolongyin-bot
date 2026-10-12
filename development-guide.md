# AutoNgoaLong v2: development guide

How to run, change, test and build AutoNgoaLong v2 from source. To use the released package, read the [README](README.md) (the Vietnamese user guide) instead.

A bot for the Flash webgame **Ngọa Long** on the private server ngoalongviet.com, played in the **Coowon** browser on Windows. It clicks in the background with `PostMessage`, captures with `PrintWindow`, finds the 1000×600 game canvas by itself, and runs each feature as a decision loop: every tick it perceives the screen stack and its numbers from UI parts (OpenCV templates, colour probes, the game's digit font; no OCR), picks one action from the feature's rules, and checks that the game answered. Every wait is time-limited and can be stopped.

Supported: Nâng cấp khoa kỹ, Nâng cấp công trình (city and harbour), Clear Phó Bản, Tháp Thí Luyện, Quay Tinh Tú, Võ Khôi Tháp, plus AutoClick.

- Users: read the Vietnamese user guide, the root [README.md](README.md). It ships with the build as `HUONG-DAN.md`.
- Design: [docs/08-engine-revamp-plan.md](docs/08-engine-revamp-plan.md) (the engine and the features, final state at the top) and [docs/06-design.md](docs/06-design.md) (everything around it). A visual tour of the code: [docs/bot-structure.html](docs/bot-structure.html). All design docs are listed in [docs/README.md](docs/README.md).

## Developer quick start

Requirements: Windows 10/11 64-bit, Python 3.12 or newer (the project is developed on 3.14 through the `py` launcher). The UI language is Vietnamese.

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m ngoalong            # start the app
.venv\Scripts\python.exe -m ngoalong --debug    # start with debug mode (overlay, step PNGs) on
.venv\Scripts\python.exe -m ngoalong --selftest # offline checks, exit code 0/1
```

Command line of `python -m ngoalong` (and of `AutoNgoaLong.exe`):

| Option | Effect |
| --- | --- |
| `--settings PATH` | `settings.toml` to use (default: next to the app) |
| `--debug` | Start with debug mode on |
| `--selftest` | Run the offline checks (profile, features, Win32 driver, the perception benchmark on the bundled corpus, offscreen main window) and exit 0 (passed) or 1 (failed) |
| `--corpus PATH` | `--selftest`: `labels.yaml` of a corpus (default: the one bundled in the build; from source `corpus/live/labels.yaml`) |
| `--report PATH` | `--selftest`: also write the report to a file (the windowed build always writes `selftest-report.txt` next to the exe) |
| `--no-ui` | `--selftest`: skip the main window check |

To try it against the game: open Coowon on the game page (click **Run this time** on the "Adobe Flash Player was blocked" bar), start the app, press **Kiểm tra**. The status lines should read `Khung game: … 1000×600 ×1.00 - tin cậy` and `Màn hình: city{…} | hud{…}` (the Snapshot).

## Tools

```powershell
.venv\Scripts\python.exe tools\perceive.py --live                    # perceive the game window now
.venv\Scripts\python.exe tools\perceive.py corpus\live --draw build\p # annotated Snapshots of stored frames
.venv\Scripts\python.exe tools\perceive.py --bench                   # the perception benchmark (must pass)
.venv\Scripts\python.exe -m ngoalong.tools.explore shot city         # capture the canvas into corpus\live\NNN-city.png
.venv\Scripts\python.exe -m ngoalong.tools.explore click 944 493 --name world-map
.venv\Scripts\python.exe -m ngoalong.tools.run_feature quay_ruong --set buy=10 --stop-after 60
.venv\Scripts\python.exe tools\cut_templates.py --check              # templates match template_crops.yaml
.venv\Scripts\python.exe tools\cut_templates.py --crops profiles\ngoalongviet\part_crops.yaml --check
.venv\Scripts\python.exe tools\part_report.py                        # every part's score over the corpus
.venv\Scripts\python.exe tools\number_report.py                      # every number field over the corpus
```

`run_feature` runs one feature through the app's runner (the same path as **Bắt đầu**) with the run log on the console; it starts two Python processes, so stop it with `--stop-after` or Ctrl+C. The corpus PNGs under `corpus/live/` are git-ignored (large); their labels (`labels.yaml`) are tracked. There is no unit-test suite: perception is checked by the benchmark over the labelled corpus, features on the live game.

## Layout

```text
ngoalong/                  the package
  app.py                   entry point, AppServices (dependency wiring, one Actor per run), command line
  paths.py                 APP_ROOT (project root / exe folder) and BUNDLE_ROOT (_internal)
  selftest.py              --selftest checks and the build's corpus bundle
  config/settings.py       pydantic settings saved as settings.toml
  core/                    GameWindow, CanvasLocator, capture/input, Matcher, TemplateStore, digit reader
    drivers/               win32.py (Coowon), fake.py (recording input), cdp.py (stub)
  perception/              canvas -> Snapshot: parts, probes, screens, readers; bench.py (the benchmark)
  engine/                  Task/Feature, the decision loop, actions, Actor, navigation, interrupts, recovery,
                           TaskRunner, RunnerManager, scheduler, history, registry
  features/                one module per feature, registered with @register
  debug/                   DebugFrame model, renderer, overlay window, sinks
  ui/                      main window, generated feature forms, tabs/ (one module per tab), widgets/
  tools/                   explore.py (live capture for the corpus), run_feature.py
  hotkeys.py  notify.py
profiles/ngoalongviet/     data, editable without rebuilding
  parts.yaml probes.yaml numbers.yaml screens/*.yaml   perception
  nav.yaml popups.yaml panels.yaml locator.yaml        navigation, interrupts, canvas anchors
  part_crops.yaml template_crops.yaml digit_samples.yaml   reproducible template cuts
  templates/**             template PNGs (+ optional .mask.png)
corpus/live/               labelled live captures (labels.yaml tracked, PNGs local)
docs/                      design documents, user guide, bot structure page, reference screenshots
tools/                     perceive.py, part_report.py, number_report.py, cut_templates.py, cut_digits.py,
                           live/livectl.cs (reference Win32 find/capture/click)
AutoNgoaLong.spec          PyInstaller spec (one-folder build)
build.ps1                  build script
```

Extension points: a feature is one file in `ngoalong/features/` with `@register` (a `Feature` with `entry`, `Memory`, `rules()` and `goal()`; the UI form is generated from its `Params` model); its screens go in `profiles/ngoalongviet/screens/<area>.yaml` over parts in `parts.yaml`, and the ways to reach them in `nav.yaml`. A UI tab is one module in `ngoalong/ui/tabs/` with `@register_tab`. Features, tabs and widgets are discovered with `pkgutil` at run time. See "Adding a feature" in [docs/bot-structure.html](docs/bot-structure.html).

## Build

```powershell
.\build.ps1                 # venv + deps if missing, PyInstaller, then AutoNgoaLong.exe --selftest
.\build.ps1 -SkipSelftest   # skip the self-test of the built exe
```

Output is a one-folder build in `dist\AutoNgoaLong\`:

| Path | Contents |
| --- | --- |
| `AutoNgoaLong.exe` | Windowed app; `--selftest` runs the offline checks, including the perception benchmark on the bundled corpus |
| `_internal\` | Python runtime, Qt, OpenCV, the `ngoalong` package, `selftest\` (57 corpus frames as lossless WebP + their `labels.yaml`) |
| `profiles\` | Copied from the source tree, next to the exe, so parts, screens, navigation and templates stay editable |
| `HUONG-DAN.md`, `VERSION.txt` | The Vietnamese user guide and the version |

The spec bundles the self-test frames (`ngoalong.selftest.BUNDLE_FRAMES`) from `corpus\live\` on the build machine; when they are missing it prints a warning and the build's self-test reports `SKIP [perception]` instead of running the benchmark. At run time the build reads and writes `settings.toml`, `logs\` (run folders and `app.log`) and `data\` (history, routines) next to the exe; see `ngoalong/paths.py`. The build script keeps those user files across rebuilds and exits non-zero when PyInstaller or the self-test fails; the self-test report is saved to `build\selftest-report.txt`. Zip `dist\AutoNgoaLong\` to distribute it. Before zipping a release, remove the user files the build script kept (`settings.toml*`, `logs\`, `data\`, `selftest-report.txt`), and `_internal\selftest\` if the corpus frames should not ship; then attach the zip to a GitHub release, for example `gh release create v2.0.0 AutoNgoaLong-v2.0.0-win64.zip`.

## Licence

MIT, see [LICENSE](LICENSE). The target server allows bots.
