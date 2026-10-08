# 02 · Architecture (v1.8.0)

The components of the current app and how they connect. Index of all parts: [README.md](README.md)

v1.8.0 is one single-threaded AutoIt program. The GUI loop calls feature routines directly. Those routines reach the game in only two ways: posted window messages going out, and window screenshots coming back.

```mermaid
flowchart TD
    GUI["GUI event loop<br/>Koda form, 4 tabs, status bar<br/>button press = blocking call"]
    HK["Global hotkeys<br/>F9: pick window + origin<br/>F10: toggle ON, F4: quit"]
    INI["NgoaLong_data.ini<br/>[PRO5] section, loaded at start<br/>written back on close"]
    FEAT["Feature routines (11)<br/>one function per button: QUAYRUONG, CLEARMAP, THAMBAO, ...<br/>click origin + offset, Sleep, poll templates<br/>loop until the count is reached or ON = False"]
    STATE["Global state<br/>HWND: the target window<br/>origin XxY (TOADO_GOC)<br/>ON / ACP_ON run flags"]
    IN["Input injection<br/>LVS_MOUSECLICK, _LVS_SEND_TEXT, ControlSend<br/>WM_LBUTTONDOWN/UP and WM_KEYDOWN<br/>lParam = y * 65536 + x; real cursor never moves"]
    VIS["Vision: ImageSearchEx<br/>PrintWindow into an off-screen bitmap<br/>BmpSearch x86 shellcode, exact pixel match<br/>returns the top-left of the first match"]
    GAME["Game window<br/>Flash plugin or browser render widget<br/>addressed only through the saved HWND"]
    TPL["Template images<br/>Server/default/All/Images<br/>79 BMP files"]

    GUI -->|button press| FEAT
    HK -->|F10 flips ON| FEAT
    INI -->|restored at start| STATE
    STATE --> FEAT
    FEAT --> IN
    FEAT --> VIS
    IN -->|PostMessage| GAME
    GAME -->|PrintWindow| VIS
    TPL -->|loaded per search| VIS
```

The whole app shares a handful of globals. Anything that overwrites `HWND`, which F2 and F9 both do, silently retargets every feature. The game never receives real mouse or keyboard input, which is why it can run behind other windows.

## Components

| Component | Source (in `app_logic_only.au3`, archived; see 01) | Role |
| --- | --- | --- |
| GUI event loop | Koda form section, main `While 1` / `GUIGetMsg` | Builds the 4 tabs, reads inputs, calls a feature on button press and blocks until it returns |
| Global hotkeys | `HotKeySet` block | F1 clear click list, F2 record point, F3 AutoClick on/off, F4 quit, F8 hide/show game, F9 capture origin, F10 start/stop |
| Settings | `_LOADDATA`, `GUI_EVENT_CLOSE` branch | Reads and writes `NgoaLong_data.ini` |
| Global state | `HWND`, `ON`, `ACP_ON`, `TITLEACTIVE`, origin label | Shared by every routine |
| Feature routines | `QUAYRUONG`, `MOTHEBAI`, `NANGCAPKHOAKY`, `NCCT_*`, `CAYCAPCHUACONG`, `THAPTHILUYEN`, `CLEARMAP`, `THAMBAO`, `BOSS_*`, `TRIKY`, `ACP_*` | One per button; details in [03 Workflows](03-workflows.md) |
| Input injection | `LVS_MOUSECLICK`, `_LVS_SEND_TEXT`, `ACP_BUTTON` | `PostMessage` mouse and key messages to `HWND` |
| Vision | `IMAGESEARCHEX*`, `_BMPSEARCH` | `PrintWindow` capture plus exact pixel match in embedded x86 code |
| Startup extras | `_CHECK_UPDATE`, IE `Shell.Explorer.2` banner | Blog scrape for new versions; remote ad page |
