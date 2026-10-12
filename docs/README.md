# AutoNgoaLong v2 — Design documents

As of 2026-10-10.

This design document is split into short files, so a person or an agent can open only the part it needs. File 06 is the design of the bot (with its implementation status), 07 its first screen reference data, and 08 the engine revamp that replaced the first engine (perceive → read state → decide; phases R0–R4, done on 2026-10-10). The bot structure page sits beside them; the user guide (Vietnamese) is the root README and the development guide is `development-guide.md` in the repository root. Reference screenshots are in [`game screenshots/`](game%20screenshots/).

## Files

| File | Read it for | Contents |
| --- | --- | --- |
| [06-design.md](06-design.md) | The design | Target browsers, automatic canvas detection, interrupts, debug overlay, Python stack, module layout, interfaces, roadmap, implementation status, decisions |
| [07-screen-catalogue.md](07-screen-catalogue.md) | Canvas measurements | Measured canvas rectangles; the first engine's 7 scenes and their offsets (historical) |
| [08-engine-revamp-plan.md](08-engine-revamp-plan.md) | The engine as it is now | Final state; why the first engine got lost live; live exploration; the six feature flows as observed; perception (parts, probes, digits, screens, readers, Snapshot), the decision loop, each feature's rules, live gates and findings per phase R0–R4 |
| [bot-structure.html](bot-structure.html) | A visual tour of the code | Interactive page: layers and modules, run life cycle, perceive → decide → act, the recovery ladder, Quay Tinh Tú as rules, profile data, feature catalogue |
| [../README.md](../README.md) | Using the app (Vietnamese) | Install, first run, window detection, the six features and AutoClick, debug mode, routines, multiple windows, troubleshooting. Ships in the build as `HUONG-DAN.md` |
| [../development-guide.md](../development-guide.md) | Working on the code | Developer quick start, command line, code layout, tools, build (`build.ps1`, `AutoNgoaLong.spec`, `--selftest`) |

## Quick facts

- The bot clicks with background `PostMessage` mouse messages and captures with `PrintWindow`, so the game window can stay behind other windows.
- It finds the 1000×600 canvas by itself and recognises screens and numbers from UI parts (templates, colour probes, the game's digit font), without OCR.
- It supports six game features (Khoa kỹ, structures in the city and the harbour, campaign, trial tower, Tinh Tú chests, the Võ Khôi Tháp loop) plus AutoClick, on Coowon and the ngoalongviet.com server, with a Vietnamese UI. FlashBrowser comes later (06 "Target browsers").

## Which file to read for a task

- **Changing or adding a feature**: 08 (the feature's flow and rules, "Perception", "Decision loop"), the feature module in `ngoalong/features/`, and the profile files in `profiles/ngoalongviet/` (`parts.yaml`, `screens/`, `nav.yaml`).
- **A screen is misread**: 08 "Perception", `tools/perceive.py` (`--live`, `--draw`, `--bench`) and `corpus/live/labels.yaml`.
- **Building input, capture or the canvas locator**: 06 "Finding the game screen automatically", "Stack" and "Core interfaces".
- **Changing settings**: `ngoalong/config/settings.py` (pydantic models saved as `settings.toml`); removed features are dropped by `Settings.forget_features`.
- **Building or releasing**: [development-guide.md](../development-guide.md) (Build section), then the implementation status in 06.
- **Helping a player**: the user guide, the root [README.md](../README.md).
- **Changing the plan**: 08 for the engine, 06 for everything around it.
