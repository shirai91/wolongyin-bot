# AutoNgoaLong — Software Design Document (Reverse-Engineered)

As of 2026-10-06. Online copy (Claude Doc): https://claude.ai/code/artifact/891ca6f5-e03c-4839-a7cc-bfe0f8e25c3b

This design document is split into seven short files, so a person or an agent can open only the part it needs. Files 01–05 describe v1.8.0 as it is today. File 06 is the working draft of the Python rewrite (with its implementation status), and 07 is its screen reference data. The v2 user guide (Vietnamese) and the developer README sit beside them. Reference screenshots are in [`game screenshots/`](game%20screenshots/).

## Files

| File | Read it for | Contents |
| --- | --- | --- |
| [01-overview-and-inventory.md](01-overview-and-inventory.md) | What the app is and what ships | How it was reverse engineered, file inventory, template folders, recovered files |
| [02-architecture.md](02-architecture.md) | How the parts connect | Architecture diagram, component table with source function names |
| [03-workflows.md](03-workflows.md) | What each feature does | Startup, F9 window capture, the shared run pattern, catalogue of 11 features |
| [04-data-and-configuration.md](04-data-and-configuration.md) | Settings and assets | INI keys, template rules, coordinate model |
| [05-known-issues.md](05-known-issues.md) | What is broken | 17 issues (#1–#17) with severity, location and effect |
| [06-rewrite-design.md](06-rewrite-design.md) | The rewrite plan | Target browsers, automatic screen detection, scene recognition, debug overlay, Python stack, module layout, interfaces, old-to-new mapping, roadmap, decisions |
| [07-screen-catalogue.md](07-screen-catalogue.md) | Where things are on screen | Measured canvas rectangles, 7 scenes with object positions, v1.8.0 offsets checked against screenshots |
| [08-engine-revamp-plan.md](08-engine-revamp-plan.md) | The next engine | Why screen detection fails live, the perceive → read state → decide loop, UI parts, screen state, rules, recovery, navigation, phases R0–R4, test policy |
| [user-guide-vi.md](user-guide-vi.md) | Using the v2 app (Vietnamese) | Install, first run, window detection, each feature, debug mode, routines, multiple windows, troubleshooting. Ships in the build as `HUONG-DAN.md` |
| [../README.md](../README.md) | Working on the v2 code | Developer quick start, command line, code layout, tests, build (`build.ps1`, `AutoNgoaLong.spec`, `--selftest`) |

## Quick facts

- v1.8.0 is a compiled AutoIt v3 script (EA06 format). Its app logic is about 1,900 lines; the rest is bundled standard libraries.
- It clicks by posting `WM_LBUTTONDOWN`/`UP` to one game window at `origin + offset`, and detects game states with exact-pixel BMP matching on `PrintWindow` captures.
- Click offsets assume the game canvas at 100% zoom, about 1000×600 px.
- The MVP targets Coowon on Windows and the ngoalongviet.com server, with a Vietnamese UI. FlashBrowser comes after the MVP. Details are under Target browsers and Decisions in 06.
- Recovered files (decompiled source, coordinates, extractor) are archived in `AutoNgoaLong-v1.8.0-legacy.zip` next to the project folder; see 01.

## Which file to read for a task

- **Porting a feature**: 03 Workflows, plus `../profiles/ngoalongviet/coords/`, plus the matching rows in 06.
- **Building input, capture or vision**: 02 Architecture, 05 Known issues (#1, #3, #9, #14), and the Core interfaces in 06.
- **Canvas detection, scenes or the debug overlay**: the three matching sections in 06, plus 07 Screen catalogue.
- **Migrating settings**: 04 Data and configuration.
- **Building or releasing**: the root [README.md](../README.md) (Build section), then the Implementation status in 06.
- **Helping a player**: the user guide, [user-guide-vi.md](user-guide-vi.md).
- **Changing the plan**: edit 06 only. Files 01–05 describe the fixed v1.8.0 build and change only when new reverse-engineering findings come in.
