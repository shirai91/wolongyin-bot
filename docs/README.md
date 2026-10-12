# AutoNgoaLong — Software Design Document (Reverse-Engineered)

As of 2026-10-10. Online copy of the first version (Claude Doc, 2026-10-06): https://claude.ai/code/artifact/891ca6f5-e03c-4839-a7cc-bfe0f8e25c3b

This design document is split into short files, so a person or an agent can open only the part it needs. Files 01–05 describe v1.8.0 as it is today. File 06 is the design of the Python rewrite (with its implementation status), 07 its first screen reference data, and 08 the engine revamp that replaced the first engine (perceive → read state → decide; phases R0–R4, done on 2026-10-10). The bot structure page sits beside them; the v2 user guide (Vietnamese) is the root README and the development guide is `development-guide.md` in the repository root. Reference screenshots are in [`game screenshots/`](game%20screenshots/).

## Files

| File | Read it for | Contents |
| --- | --- | --- |
| [01-overview-and-inventory.md](01-overview-and-inventory.md) | What the app is and what ships | How it was reverse engineered, file inventory, template folders, recovered files |
| [02-architecture.md](02-architecture.md) | How the parts connect | Architecture diagram, component table with source function names |
| [03-workflows.md](03-workflows.md) | What each feature does | Startup, F9 window capture, the shared run pattern, catalogue of 11 features |
| [04-data-and-configuration.md](04-data-and-configuration.md) | Settings and assets | INI keys, template rules, coordinate model |
| [05-known-issues.md](05-known-issues.md) | What is broken | 17 issues (#1–#17) with severity, location and effect |
| [06-rewrite-design.md](06-rewrite-design.md) | The rewrite | Target browsers, automatic canvas detection, interrupts, debug overlay, Python stack, module layout, interfaces, old-to-new mapping, roadmap, implementation status, decisions |
| [07-screen-catalogue.md](07-screen-catalogue.md) | Canvas measurements | Measured canvas rectangles; the first engine's 7 scenes and v1.8.0 offsets (historical) |
| [08-engine-revamp-plan.md](08-engine-revamp-plan.md) | The engine as it is now | Final state; why the first engine got lost live; live exploration; the six feature flows as observed; perception (parts, probes, digits, screens, readers, Snapshot), the decision loop, each feature's rules, live gates and findings per phase R0–R4 |
| [bot-structure.html](bot-structure.html) | A visual tour of the code | Interactive page: layers and modules, run life cycle, perceive → decide → act, the recovery ladder, Quay Tinh Tú as rules, profile data, feature catalogue |
| [../README.md](../README.md) | Using the v2 app (Vietnamese) | Install, first run, window detection, the six features and AutoClick, debug mode, routines, multiple windows, troubleshooting. Ships in the build as `HUONG-DAN.md` |
| [../development-guide.md](../development-guide.md) | Working on the v2 code | Developer quick start, command line, code layout, tools, build (`build.ps1`, `AutoNgoaLong.spec`, `--selftest`) |

## Quick facts

- v1.8.0 is a compiled AutoIt v3 script (EA06 format). Its app logic is about 1,900 lines; the rest is bundled standard libraries.
- It clicks by posting `WM_LBUTTONDOWN`/`UP` to one game window at `origin + offset`, and detects game states with exact-pixel BMP matching on `PrintWindow` captures.
- v2 keeps the background `PostMessage` clicks and `PrintWindow` captures, finds the 1000×600 canvas by itself, and recognises screens and numbers from UI parts (templates, colour probes, the game's digit font), without OCR.
- v2 supports six game features (Khoa kỹ, structures in the city and the harbour, campaign, trial tower, Tinh Tú chests, the Võ Khôi Tháp loop) plus AutoClick, on Coowon and the ngoalongviet.com server, with a Vietnamese UI. FlashBrowser comes later (06 "Target browsers").
- Recovered files (decompiled source, coordinates, extractor) are archived in `AutoNgoaLong-v1.8.0-legacy.zip` next to the project folder; see 01.

## Which file to read for a task

- **Changing or adding a feature**: 08 (the feature's flow and rules, "Perception", "Decision loop"), the feature module in `ngoalong/features/`, and the profile files in `profiles/ngoalongviet/` (`parts.yaml`, `screens/`, `nav.yaml`).
- **A screen is misread**: 08 "Perception", `tools/perceive.py` (`--live`, `--draw`, `--bench`) and `corpus/live/labels.yaml`.
- **Building input, capture or the canvas locator**: 02 Architecture, 05 Known issues (#1, #3, #9, #14), 06 "Finding the game screen automatically" and "Core interfaces".
- **Migrating settings**: 04 Data and configuration; removed features are dropped by `Settings.forget_features`.
- **Building or releasing**: [development-guide.md](../development-guide.md) (Build section), then the implementation status in 06.
- **Helping a player**: the user guide, the root [README.md](../README.md).
- **Changing the plan**: 08 for the engine, 06 for everything around it. Files 01–05 describe the fixed v1.8.0 build and change only when new reverse-engineering findings come in.
